import {
  type DuckDBConnection,
  type DuckDBValue,
  listValue,
} from "@duckdb/node-api";
import type {
  Bundle,
  DeclaredLogicalType,
  FrontmatterValue,
} from "@franklinbaldo/okf-parser";

import type { DeclaredCatalogSchema } from "./declared.js";
import { quotedIdentifier, validateSchemaName } from "./normalized.js";

const INTERNAL_COLUMNS = [
  "__okf_path",
  "__okf_concept_id",
  "__okf_logical_key",
  "__okf_body",
  "__okf_body_lines",
] as const;
const OKF_PREFIX = "__okf_";

export interface TypedMaterializeOptions {
  readonly schema?: string;
  readonly overwrite?: boolean;
}

export interface TypedMaterialization {
  readonly schema: string;
  readonly tables: readonly string[];
  readonly unrecognized_tables: readonly string[];
}

interface TypedFieldPlan {
  readonly name: string;
  readonly declaredType?: DeclaredLogicalType;
  readonly rawName?: string;
  readonly comment?: string;
}

interface TypedTablePlan {
  readonly conceptType: string;
  readonly fields: readonly TypedFieldPlan[];
  readonly tableComment: string | null;
}

interface ConceptRow {
  readonly path: string;
  readonly conceptId: string;
  readonly logicalKey: string;
  readonly body: string;
  readonly frontmatter: Readonly<Record<string, FrontmatterValue>>;
}

interface ExistingComments {
  readonly table: string | null;
  readonly columns: Readonly<Record<string, string>>;
}

export class TypedTableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TypedTableError";
  }
}

export class TypedTableCollisionError extends TypedTableError {
  readonly schemaName: string;
  readonly tables: readonly string[];

  constructor(schemaName: string, tables: readonly string[]) {
    super(`schema ${JSON.stringify(schemaName)} already contains ${tables.join(", ")}`);
    this.name = "TypedTableCollisionError";
    this.schemaName = schemaName;
    this.tables = Object.freeze([...tables]);
  }
}

export function duckDbIdentifierKey(identifier: string): string {
  return [...identifier].map((character) =>
    character >= "A" && character <= "Z" ? character.toLowerCase() : character
  ).join("");
}

function isReserved(name: string): boolean {
  return name.slice(0, OKF_PREFIX.length).toLowerCase() === OKF_PREFIX;
}

function quoteLiteral(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

function parseFrontmatter(value: string): Readonly<Record<string, FrontmatterValue>> {
  const parsed: unknown = JSON.parse(value);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new TypedTableError("serialized frontmatter is not an object");
  }
  return parsed as Readonly<Record<string, FrontmatterValue>>;
}

function rowsByType(bundle: Bundle): Map<string, ConceptRow[]> {
  const result = new Map<string, ConceptRow[]>();
  for (const concept of bundle.concepts) {
    const rows = result.get(concept.conceptType) ?? [];
    rows.push({
      path: concept.path,
      conceptId: concept.conceptId,
      logicalKey: concept.logicalKey,
      body: concept.body,
      frontmatter: parseFrontmatter(concept.frontmatterJson),
    });
    result.set(concept.conceptType, rows);
  }
  return result;
}

function declaredAliases(
  frontmatter: Readonly<Record<string, FrontmatterValue>>,
  declared: Readonly<Record<string, DeclaredLogicalType>>,
): Readonly<Record<string, string>> {
  const byKey = new Map(Object.keys(declared).map((name) => [duckDbIdentifierKey(name), name]));
  const aliases: Record<string, string> = {};
  for (const authored of Object.keys(frontmatter)) {
    const canonical = byKey.get(duckDbIdentifierKey(authored));
    if (canonical === undefined) continue;
    const previous = aliases[canonical];
    if (previous !== undefined && previous !== authored) {
      throw new TypedTableError(
        `frontmatter contains fields ${JSON.stringify(previous)} and ${JSON.stringify(authored)} ` +
        `that collide with declared column ${JSON.stringify(canonical)} under DuckDB identifier equality`,
      );
    }
    aliases[canonical] = authored;
  }
  return aliases;
}

function buildPlan(
  conceptType: string,
  rows: readonly ConceptRow[],
  declaration: DeclaredCatalogSchema,
): TypedTablePlan {
  const declared = Object.fromEntries(
    Object.entries(declaration.columns).filter(([name]) => !isReserved(name)),
  );
  const declaredKeys = new Set(Object.keys(declared).map(duckDbIdentifierKey));
  const spellings = new Map<string, Set<string>>();
  const structured = new Set<string>();
  for (const row of rows) {
    declaredAliases(row.frontmatter, declared);
    for (const [name, value] of Object.entries(row.frontmatter)) {
      const key = duckDbIdentifierKey(name);
      if (declaredKeys.has(key) || isReserved(name)) continue;
      const names = spellings.get(key) ?? new Set<string>();
      names.add(name);
      spellings.set(key, names);
      if (!(typeof value === "string" || value === null)) structured.add(key);
    }
  }

  const fields: TypedFieldPlan[] = Object.entries(declared).map(([name, declaredType]) => ({
    name,
    declaredType,
    rawName: `__okf_raw_${name}`,
    ...(declaration.columnComments[name] === undefined
      ? {}
      : { comment: declaration.columnComments[name] }),
  }));
  for (const [key, names] of [...spellings].sort(([left], [right]) => left.localeCompare(right, "en"))) {
    if (structured.has(key)) continue;
    if (names.size !== 1) {
      throw new TypedTableError(
        `type ${JSON.stringify(conceptType)} has undeclared fields that collide under DuckDB identifier equality: ` +
        [...names].sort().map((name) => JSON.stringify(name)).join(", "),
      );
    }
    const [name] = [...names];
    if (name !== undefined) fields.push({ name });
  }
  return {
    conceptType,
    fields: Object.freeze(fields),
    tableComment: declaration.tableComment,
  };
}

function rawSqlType(logicalType: DeclaredLogicalType): string {
  return logicalType.family === "list" ? "VARCHAR[]" : "VARCHAR";
}

function ddl(plan: TypedTablePlan, schema: string): string {
  const columns = [
    '"__okf_path" VARCHAR',
    '"__okf_concept_id" VARCHAR',
    '"__okf_logical_key" VARCHAR',
    '"__okf_body" VARCHAR',
    '"__okf_body_lines" VARCHAR[]',
  ];
  for (const field of plan.fields) {
    if (field.declaredType === undefined || field.rawName === undefined) {
      columns.push(`${quotedIdentifier(field.name)} VARCHAR`);
    } else {
      columns.push(`${quotedIdentifier(field.rawName)} ${rawSqlType(field.declaredType)}`);
      columns.push(`${quotedIdentifier(field.name)} ${field.declaredType.sql}`);
    }
  }
  return `CREATE TABLE ${quotedIdentifier(schema)}.${quotedIdentifier(plan.conceptType)} (${columns.join(", ")})`;
}

function splitLines(body: string): readonly string[] {
  if (body === "") return [];
  const normalized = body.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
  const lines = normalized.split("\n");
  if (lines.at(-1) === "") lines.pop();
  return lines;
}

function rawDeclaredValue(
  value: FrontmatterValue | undefined,
  logicalType: DeclaredLogicalType,
): DuckDBValue {
  if (value === undefined || value === null) return null;
  if (logicalType.family === "list") {
    if (Array.isArray(value) && value.every((item) => typeof item === "string" || item === null)) {
      return listValue(value);
    }
    return null;
  }
  return typeof value === "string" ? value : null;
}

function rowValues(row: ConceptRow, plan: TypedTablePlan): readonly DuckDBValue[] {
  const declared = Object.fromEntries(
    plan.fields
      .filter((field): field is TypedFieldPlan & { declaredType: DeclaredLogicalType } => field.declaredType !== undefined)
      .map((field) => [field.name, field.declaredType]),
  );
  const aliases = declaredAliases(row.frontmatter, declared);
  const values: DuckDBValue[] = [
    row.path,
    row.conceptId,
    row.logicalKey,
    row.body,
    listValue([...splitLines(row.body)]),
  ];
  for (const field of plan.fields) {
    if (field.declaredType !== undefined) {
      const authored = aliases[field.name];
      values.push(rawDeclaredValue(authored === undefined ? undefined : row.frontmatter[authored], field.declaredType));
    } else {
      const value = row.frontmatter[field.name];
      values.push(typeof value === "string" || value === null ? value : null);
    }
  }
  return values;
}

function insertColumns(plan: TypedTablePlan): readonly string[] {
  return [
    ...INTERNAL_COLUMNS,
    ...plan.fields.map((field) => field.declaredType === undefined ? field.name : field.rawName ?? field.name),
  ];
}

async function insertRows(
  connection: DuckDBConnection,
  schema: string,
  plan: TypedTablePlan,
  rows: readonly ConceptRow[],
): Promise<void> {
  if (rows.length === 0) return;
  const columns = insertColumns(plan);
  const placeholders = columns.map((_, index) => `$${index + 1}`).join(", ");
  const sql = `INSERT INTO ${quotedIdentifier(schema)}.${quotedIdentifier(plan.conceptType)} (` +
    `${columns.map(quotedIdentifier).join(", ")}) VALUES (${placeholders})`;
  for (const row of rows) await connection.run(sql, [...rowValues(row, plan)]);
}

async function populateTyped(
  connection: DuckDBConnection,
  schema: string,
  plan: TypedTablePlan,
): Promise<void> {
  const assignments = plan.fields.flatMap((field) => {
    if (field.declaredType === undefined || field.rawName === undefined) return [];
    return [
      `${quotedIdentifier(field.name)} = TRY_CAST(${quotedIdentifier(field.rawName)} AS ${field.declaredType.sql})`,
    ];
  });
  if (assignments.length === 0) return;
  await connection.run(
    `UPDATE ${quotedIdentifier(schema)}.${quotedIdentifier(plan.conceptType)} SET ${assignments.join(", ")}`,
  );
}

async function existingComments(
  connection: DuckDBConnection,
  schema: string,
  table: string,
): Promise<ExistingComments> {
  const tableReader = await connection.runAndReadAll(
    "SELECT comment FROM duckdb_tables() WHERE schema_name = $schema AND table_name = $table",
    { schema, table },
  );
  const tableValue = tableReader.getRowsJS()[0]?.[0];
  const tableComment = typeof tableValue === "string" ? tableValue : null;

  const columnReader = await connection.runAndReadAll(
    "SELECT column_name, comment FROM duckdb_columns() " +
      "WHERE schema_name = $schema AND table_name = $table AND comment IS NOT NULL",
    { schema, table },
  );
  const columns: Record<string, string> = {};
  for (const row of columnReader.getRowsJS()) {
    if (typeof row[0] === "string" && typeof row[1] === "string") columns[row[0]] = row[1];
  }
  return { table: tableComment, columns };
}

async function applyComments(
  connection: DuckDBConnection,
  schema: string,
  plan: TypedTablePlan,
  prior: ExistingComments,
): Promise<void> {
  const tableComment = plan.tableComment ?? prior.table;
  if (tableComment !== null) {
    await connection.run(
      `COMMENT ON TABLE ${quotedIdentifier(schema)}.${quotedIdentifier(plan.conceptType)} IS ${quoteLiteral(tableComment)}`,
    );
  }
  for (const field of plan.fields) {
    const comment = field.comment ?? prior.columns[field.name];
    if (comment === undefined) continue;
    await connection.run(
      `COMMENT ON COLUMN ${quotedIdentifier(schema)}.${quotedIdentifier(plan.conceptType)}.${quotedIdentifier(field.name)} IS ${quoteLiteral(comment)}`,
    );
  }
}

async function tableNames(connection: DuckDBConnection, schema: string): Promise<readonly string[]> {
  const reader = await connection.runAndReadAll(
    "SELECT table_name FROM information_schema.tables WHERE table_schema = $schema ORDER BY table_name",
    { schema },
  );
  return reader.getRowsJS().map((row) => row[0]).filter((value): value is string => typeof value === "string");
}

function matchingExisting(existing: readonly string[], name: string): string | undefined {
  const key = duckDbIdentifierKey(name);
  const matches = existing.filter((item) => duckDbIdentifierKey(item) === key);
  if (matches.length > 1) {
    throw new TypedTableError(`schema contains more than one table resolving as ${JSON.stringify(name)}`);
  }
  return matches[0];
}

export async function materializeTypedBundle(
  connection: DuckDBConnection,
  bundle: Bundle,
  declarations: Readonly<Record<string, DeclaredCatalogSchema>>,
  options: TypedMaterializeOptions = {},
): Promise<TypedMaterialization> {
  const schema = options.schema ?? "okf_types";
  validateSchemaName(schema);
  const rows = rowsByType(bundle);
  const plans = Object.entries(declarations)
    .sort(([left], [right]) => left.localeCompare(right, "en"))
    .map(([conceptType, declaration]) => buildPlan(conceptType, rows.get(conceptType) ?? [], declaration));
  const plannedKeys = new Map<string, string>();
  for (const plan of plans) {
    const key = duckDbIdentifierKey(plan.conceptType);
    const previous = plannedKeys.get(key);
    if (previous !== undefined && previous !== plan.conceptType) {
      throw new TypedTableError(
        `concept types ${JSON.stringify(previous)} and ${JSON.stringify(plan.conceptType)} collide under DuckDB identifier equality`,
      );
    }
    plannedKeys.set(key, plan.conceptType);
  }

  const existing = await tableNames(connection, schema);
  const collisions = plans.flatMap((plan) => {
    const match = matchingExisting(existing, plan.conceptType);
    return match === undefined ? [] : [match];
  });
  if (collisions.length > 0 && options.overwrite !== true) {
    throw new TypedTableCollisionError(schema, [...collisions].sort());
  }
  const unrecognized = existing.filter((name) => !plannedKeys.has(duckDbIdentifierKey(name)));

  await connection.run("BEGIN TRANSACTION");
  try {
    await connection.run("SET TimeZone = 'UTC'");
    await connection.run(`CREATE SCHEMA IF NOT EXISTS ${quotedIdentifier(schema)}`);
    for (const plan of plans) {
      const existingName = matchingExisting(existing, plan.conceptType);
      const prior = existingName === undefined
        ? { table: null, columns: {} }
        : await existingComments(connection, schema, existingName);
      if (existingName !== undefined) {
        await connection.run(`DROP TABLE ${quotedIdentifier(schema)}.${quotedIdentifier(existingName)}`);
      }
      await connection.run(ddl(plan, schema));
      await insertRows(connection, schema, plan, rows.get(plan.conceptType) ?? []);
      await populateTyped(connection, schema, plan);
      await applyComments(connection, schema, plan, prior);
    }
    await connection.run("COMMIT");
  } catch (error) {
    await connection.run("ROLLBACK");
    throw error;
  }

  return Object.freeze({
    schema,
    tables: Object.freeze(plans.map((plan) => plan.conceptType)),
    unrecognized_tables: Object.freeze(unrecognized),
  });
}
