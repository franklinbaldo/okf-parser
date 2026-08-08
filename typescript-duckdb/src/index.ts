export {
  DeclaredSchemaError,
  compileDeclaredTypeContracts,
  exportDeclaredJsonSchema,
  loadDeclaredTypes,
} from "./declared.js";
export type { DeclaredSchemaOptions } from "./declared.js";

import path from "node:path";

import {
  type DuckDBAppender,
  type DuckDBConnection,
  DuckDBInstance,
} from "@duckdb/node-api";
import {
  type Bundle,
  type Diagnostic,
  type LoadOptions,
  loadBundle,
} from "okf-parser";

const IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]*$/u;
const TABLE_NAMES = ["concepts", "links", "reserved", "diagnostics"] as const;

type TableName = (typeof TABLE_NAMES)[number];

export interface AttachOptions extends LoadOptions {
  readonly schema?: string;
  readonly overwrite?: boolean;
}

export interface ExportOptions extends AttachOptions {
  readonly database: string;
}

export interface ExportReport {
  readonly schema: string;
  readonly root: string;
  readonly conformant: boolean;
  readonly markdown_count: number;
  readonly concept_count: number;
  readonly link_count: number;
  readonly diagnostic_count: number;
}

export interface FileExportReport extends ExportReport {
  readonly database: string;
}

export class BundleExportError extends Error {
  readonly schemaName: string;
  readonly tables: readonly TableName[];

  constructor(schemaName: string, tables: readonly TableName[]) {
    super(
      `schema ${JSON.stringify(schemaName)} already contains ${tables.join(", ")}; ` +
        "pass overwrite: true or choose another database or schema",
    );
    this.name = "BundleExportError";
    this.schemaName = schemaName;
    this.tables = Object.freeze([...tables]);
  }
}

function validateSchemaName(schema: string): void {
  if (!IDENTIFIER_RE.test(schema)) {
    throw new TypeError(`invalid DuckDB schema name: ${JSON.stringify(schema)}`);
  }
}

function quotedIdentifier(value: string): string {
  return `"${value}"`;
}

function qualified(schema: string, table: string): string {
  return `${quotedIdentifier(schema)}.${quotedIdentifier(table)}`;
}

async function existingTables(
  connection: DuckDBConnection,
  schema: string,
): Promise<readonly TableName[]> {
  const reader = await connection.runAndReadAll(
    "SELECT table_name FROM information_schema.tables WHERE table_schema = $schema",
    { schema },
  );
  const present = new Set(
    reader
      .getRowsJS()
      .map((row) => row[0])
      .filter((value): value is string => typeof value === "string"),
  );
  return TABLE_NAMES.filter((name) => present.has(name));
}

function appendNullableVarchar(appender: DuckDBAppender, value: string | null): void {
  if (value === null) appender.appendNull();
  else appender.appendVarchar(value);
}

async function replaceConcepts(
  connection: DuckDBConnection,
  schema: string,
  bundle: Bundle,
): Promise<void> {
  const table = qualified(schema, "concepts");
  await connection.run(`DROP TABLE IF EXISTS ${table}`);
  await connection.run(
    `CREATE TABLE ${table} (` +
      "concept_id VARCHAR NOT NULL, logical_key VARCHAR NOT NULL, path VARCHAR NOT NULL, " +
      "concept_type VARCHAR NOT NULL, title VARCHAR, description VARCHAR, " +
      "frontmatter_json VARCHAR NOT NULL, body VARCHAR NOT NULL)",
  );
  const appender = await connection.createAppender("concepts", schema);
  try {
    for (const row of bundle.concepts) {
      appender.appendVarchar(row.conceptId);
      appender.appendVarchar(row.logicalKey);
      appender.appendVarchar(row.path);
      appender.appendVarchar(row.conceptType);
      appendNullableVarchar(appender, row.title);
      appendNullableVarchar(appender, row.description);
      appender.appendVarchar(row.frontmatterJson);
      appender.appendVarchar(row.body);
      appender.endRow();
    }
  } finally {
    appender.closeSync();
  }
}

async function replaceLinks(
  connection: DuckDBConnection,
  schema: string,
  bundle: Bundle,
): Promise<void> {
  const table = qualified(schema, "links");
  await connection.run(`DROP TABLE IF EXISTS ${table}`);
  await connection.run(
    `CREATE TABLE ${table} (` +
      "source_id VARCHAR NOT NULL, raw_target VARCHAR NOT NULL, target_id VARCHAR, " +
      "exists BOOLEAN NOT NULL, origin VARCHAR NOT NULL)",
  );
  const appender = await connection.createAppender("links", schema);
  try {
    for (const row of bundle.links) {
      appender.appendVarchar(row.sourceId);
      appender.appendVarchar(row.rawTarget);
      appendNullableVarchar(appender, row.targetId);
      appender.appendBoolean(row.exists);
      appender.appendVarchar(row.origin);
      appender.endRow();
    }
  } finally {
    appender.closeSync();
  }
}

async function replaceReserved(
  connection: DuckDBConnection,
  schema: string,
  bundle: Bundle,
): Promise<void> {
  const table = qualified(schema, "reserved");
  await connection.run(`DROP TABLE IF EXISTS ${table}`);
  await connection.run(
    `CREATE TABLE ${table} (` +
      "path VARCHAR NOT NULL, filename VARCHAR NOT NULL, body VARCHAR NOT NULL)",
  );
  const appender = await connection.createAppender("reserved", schema);
  try {
    for (const row of bundle.reserved) {
      appender.appendVarchar(row.path);
      appender.appendVarchar(row.filename);
      appender.appendVarchar(row.body);
      appender.endRow();
    }
  } finally {
    appender.closeSync();
  }
}

function orderedDiagnostics(diagnostics: readonly Diagnostic[]): readonly Diagnostic[] {
  return [...diagnostics].sort((left, right) => {
    const leftKey = [left.path, left.severity, left.code, left.message].join("\u0000");
    const rightKey = [right.path, right.severity, right.code, right.message].join("\u0000");
    return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
  });
}

async function replaceDiagnostics(
  connection: DuckDBConnection,
  schema: string,
  bundle: Bundle,
): Promise<void> {
  const table = qualified(schema, "diagnostics");
  await connection.run(`DROP TABLE IF EXISTS ${table}`);
  await connection.run(
    `CREATE TABLE ${table} (` +
      "code VARCHAR NOT NULL, severity VARCHAR NOT NULL, path VARCHAR NOT NULL, " +
      "message VARCHAR NOT NULL)",
  );
  const appender = await connection.createAppender("diagnostics", schema);
  try {
    for (const row of orderedDiagnostics(bundle.diagnostics)) {
      appender.appendVarchar(row.code);
      appender.appendVarchar(row.severity);
      appender.appendVarchar(row.path);
      appender.appendVarchar(row.message);
      appender.endRow();
    }
  } finally {
    appender.closeSync();
  }
}

export async function attachOkf(
  connection: DuckDBConnection,
  root: string | URL,
  options: AttachOptions = {},
): Promise<ExportReport> {
  const schema = options.schema ?? "okf";
  validateSchemaName(schema);
  const bundle = await loadBundle(root, {
    ...(options.exclude === undefined ? {} : { exclude: options.exclude }),
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
  const collisions = await existingTables(connection, schema);
  if (collisions.length > 0 && options.overwrite !== true) {
    throw new BundleExportError(schema, collisions);
  }

  await connection.run("BEGIN TRANSACTION");
  try {
    await connection.run(`CREATE SCHEMA IF NOT EXISTS ${quotedIdentifier(schema)}`);
    await replaceConcepts(connection, schema, bundle);
    await replaceLinks(connection, schema, bundle);
    await replaceReserved(connection, schema, bundle);
    await replaceDiagnostics(connection, schema, bundle);
    await connection.run("COMMIT");
  } catch (error) {
    await connection.run("ROLLBACK");
    throw error;
  }

  return {
    schema,
    root: bundle.root,
    conformant: !bundle.diagnostics.some((item) => item.severity === "error"),
    markdown_count: bundle.markdownCount,
    concept_count: bundle.concepts.length,
    link_count: bundle.links.length,
    diagnostic_count: bundle.diagnostics.length,
  };
}

export async function exportDuckDb(
  root: string | URL,
  options: ExportOptions,
): Promise<FileExportReport> {
  const database = path.resolve(options.database);
  const instance = await DuckDBInstance.create(database);
  const connection = await instance.connect();
  try {
    const report = await attachOkf(connection, root, options);
    return { ...report, database };
  } finally {
    connection.closeSync();
  }
}
