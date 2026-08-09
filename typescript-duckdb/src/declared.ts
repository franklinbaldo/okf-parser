import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { DuckDBConnection } from "@duckdb/node-api";
import {
  type Bundle,
  type DeclaredLogicalType,
  type DeclaredTypesByType,
  type SchemaOptions,
  type SchemaReport,
  type TypeContract,
  compileBundleTypeContracts,
  exportBundleJsonSchema,
  loadBundle,
  specRelativePath,
} from "okf-parser";

export class DeclaredSchemaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DeclaredSchemaError";
  }
}

export interface DeclaredSchemaOptions extends SchemaOptions {
  readonly specTemplate: string;
}

export interface DeclaredCatalogSchema {
  readonly tableName: string;
  readonly columns: Readonly<Record<string, DeclaredLogicalType>>;
  readonly tableComment: string | null;
  readonly columnComments: Readonly<Record<string, string>>;
}

const DECIMAL_RE = /^DECIMAL\((\d+),\s*(\d+)\)$/u;

function identifierKey(value: string): string {
  return [...value].map((character) =>
    character >= "A" && character <= "Z" ? character.toLowerCase() : character
  ).join("");
}

function declaredSchemaRelativePath(specTemplate: string, conceptType: string): string | null {
  const relative = specRelativePath(specTemplate, conceptType);
  if (relative === null) return null;
  const basename = path.posix.basename(relative);
  const extension = basename.lastIndexOf(".");
  const stem = extension < 0 ? relative : relative.slice(0, relative.length - (basename.length - extension));
  return `${stem}.schema.sql`;
}

function logicalType(dataType: string, precision?: number, scale?: number): DeclaredLogicalType {
  const sql = dataType.trim().replace(/\s+/gu, " ");
  const key = sql.toUpperCase();
  if (key.endsWith("[]")) {
    return Object.freeze({ sql, family: "list", element: logicalType(sql.slice(0, -2)) });
  }
  const decimal = DECIMAL_RE.exec(key);
  if (decimal !== null) {
    const parsedPrecision = Number(decimal[1]);
    const parsedScale = Number(decimal[2]);
    return Object.freeze({
      sql,
      family: "decimal",
      precision: precision ?? parsedPrecision,
      scale: scale ?? parsedScale,
    });
  }
  const integers = new Set(["TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT"]);
  const safeIntegers = new Set(["TINYINT", "SMALLINT", "INTEGER", "UTINYINT", "USMALLINT", "UINTEGER"]);
  if (integers.has(key)) return Object.freeze({ sql, family: "integer", isJsSafeInteger: safeIntegers.has(key) });
  const families: Readonly<Record<string, DeclaredLogicalType["family"]>> = {
    VARCHAR: "string",
    BOOLEAN: "boolean",
    FLOAT: "float",
    REAL: "float",
    DOUBLE: "float",
    DATE: "date",
    TIMESTAMP: "timestamp",
    TIMESTAMP_S: "timestamp",
    TIMESTAMP_MS: "timestamp",
    TIMESTAMP_NS: "timestamp",
    TIMESTAMPTZ: "timestamptz",
    "TIMESTAMP WITH TIME ZONE": "timestamptz",
    UUID: "uuid",
  };
  return Object.freeze({ sql, family: families[key] ?? "other" });
}

async function isFile(candidate: string): Promise<boolean> {
  try {
    return (await stat(candidate)).isFile();
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
}

export async function parseDeclaredSchema(
  sqlText: string,
  conceptType: string,
): Promise<DeclaredCatalogSchema> {
  const connection = await DuckDBConnection.create();
  try {
    await connection.run("SET TimeZone = 'UTC'");
    try {
      await connection.run(sqlText);
    } catch (error) {
      throw new DeclaredSchemaError(`declared schema script failed: ${error instanceof Error ? error.message : String(error)}`);
    }
    const tableReader = await connection.runAndReadAll(
      "SELECT database_oid, schema_oid, table_oid, table_name, comment FROM duckdb_tables() WHERE NOT temporary"
    );
    const matches = tableReader.getRowsJS().filter((row) =>
      typeof row[3] === "string" && identifierKey(row[3]) === identifierKey(conceptType)
    );
    if (matches.length === 0) {
      throw new DeclaredSchemaError(`declared schema script did not leave behind a table named ${JSON.stringify(conceptType)}`);
    }
    if (matches.length > 1) {
      throw new DeclaredSchemaError(`declared schema script left more than one table named ${JSON.stringify(conceptType)}`);
    }
    const [databaseOid, schemaOid, tableOid, tableName, tableComment] = matches[0] ?? [];
    if (!((typeof databaseOid === "number" || typeof databaseOid === "bigint") &&
      (typeof schemaOid === "number" || typeof schemaOid === "bigint") &&
      (typeof tableOid === "number" || typeof tableOid === "bigint"))) {
      throw new DeclaredSchemaError("DuckDB catalog returned non-numeric table identity");
    }
    if (typeof tableName !== "string") {
      throw new DeclaredSchemaError("DuckDB catalog returned a non-string table name");
    }
    if (tableComment !== null && typeof tableComment !== "string") {
      throw new DeclaredSchemaError("DuckDB catalog returned a non-string table comment");
    }
    const columnsReader = await connection.runAndReadAll(
      "SELECT column_name, data_type, comment, numeric_precision, numeric_scale FROM duckdb_columns() " +
      "WHERE database_oid = $databaseOid AND schema_oid = $schemaOid AND table_oid = $tableOid ORDER BY column_index",
      { databaseOid, schemaOid, tableOid },
    );
    const columns: Record<string, DeclaredLogicalType> = {};
    const columnComments: Record<string, string> = {};
    for (const row of columnsReader.getRowsJS()) {
      if (typeof row[0] !== "string" || typeof row[1] !== "string") continue;
      columns[row[0]] = logicalType(
        row[1],
        typeof row[3] === "number" ? row[3] : undefined,
        typeof row[4] === "number" ? row[4] : undefined,
      );
      if (typeof row[2] === "string") columnComments[row[0]] = row[2];
    }
    return Object.freeze({
      tableName,
      columns: Object.freeze(columns),
      tableComment,
      columnComments: Object.freeze(columnComments),
    });
  } finally {
    connection.closeSync();
  }
}

function resolveRoot(rootInput: string | URL): string {
  return path.resolve(rootInput instanceof URL ? fileURLToPath(rootInput) : rootInput);
}

export async function loadDeclaredSchemas(
  rootInput: string | URL,
  conceptTypes: readonly string[],
  specTemplate: string,
): Promise<Readonly<Record<string, DeclaredCatalogSchema>>> {
  const root = resolveRoot(rootInput);
  const ownersByPath = new Map<string, string[]>();
  for (const conceptType of conceptTypes) {
    const relative = declaredSchemaRelativePath(specTemplate, conceptType);
    if (relative !== null) ownersByPath.set(relative, [...(ownersByPath.get(relative) ?? []), conceptType]);
  }
  const declarations: Record<string, DeclaredCatalogSchema> = {};
  for (const [relative, owners] of [...ownersByPath].sort(([left], [right]) => left.localeCompare(right, "en"))) {
    const declarationPath = path.join(root, relative);
    if (!(await isFile(declarationPath))) continue;
    if (owners.length > 1) {
      throw new DeclaredSchemaError(
        `declared schema path ${JSON.stringify(relative)} is shared by concept types ${owners.sort().map((owner) => JSON.stringify(owner)).join(", ")}`
      );
    }
    const conceptType = owners[0];
    if (conceptType === undefined) continue;
    declarations[conceptType] = await parseDeclaredSchema(await readFile(declarationPath, "utf8"), conceptType);
  }
  return Object.freeze(declarations);
}

export async function loadDeclaredTypes(
  rootInput: string | URL,
  conceptTypes: readonly string[],
  specTemplate: string,
): Promise<DeclaredTypesByType> {
  const schemas = await loadDeclaredSchemas(rootInput, conceptTypes, specTemplate);
  return Object.freeze(Object.fromEntries(
    Object.entries(schemas).map(([conceptType, schema]) => [conceptType, schema.columns]),
  ));
}

function schemaOptions(
  options: DeclaredSchemaOptions,
  declaredTypesByType: DeclaredTypesByType,
): SchemaOptions {
  return {
    ...(options.exclude === undefined ? {} : { exclude: options.exclude }),
    ...(options.signal === undefined ? {} : { signal: options.signal }),
    ...(options.inferTypes === undefined ? {} : { inferTypes: options.inferTypes }),
    ...(options.casts === undefined ? {} : { casts: options.casts }),
    declaredTypesByType,
  };
}

async function declaredOptionsForBundle(
  bundle: Bundle,
  options: DeclaredSchemaOptions,
): Promise<SchemaOptions> {
  const conceptTypes = [...new Set(bundle.concepts.map((concept) => concept.conceptType))];
  const declaredTypesByType = await loadDeclaredTypes(bundle.root, conceptTypes, options.specTemplate);
  return schemaOptions(options, declaredTypesByType);
}

export async function compileDeclaredBundleTypeContracts(
  bundle: Bundle,
  options: DeclaredSchemaOptions,
): Promise<readonly TypeContract[]> {
  return compileBundleTypeContracts(bundle, await declaredOptionsForBundle(bundle, options));
}

export async function compileDeclaredTypeContracts(
  root: string | URL,
  options: DeclaredSchemaOptions,
): Promise<readonly TypeContract[]> {
  const bundle = await loadBundle(root, options);
  return compileDeclaredBundleTypeContracts(bundle, options);
}

export async function exportDeclaredBundleJsonSchema(
  bundle: Bundle,
  options: DeclaredSchemaOptions,
): Promise<SchemaReport> {
  return exportBundleJsonSchema(bundle, await declaredOptionsForBundle(bundle, options));
}

export async function exportDeclaredJsonSchema(
  root: string | URL,
  options: DeclaredSchemaOptions,
): Promise<SchemaReport> {
  const bundle = await loadBundle(root, options);
  return exportDeclaredBundleJsonSchema(bundle, options);
}
