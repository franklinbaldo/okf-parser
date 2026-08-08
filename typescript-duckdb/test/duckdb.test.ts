import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { DuckDBConnection, DuckDBInstance } from "@duckdb/node-api";
import { afterEach, expect, test } from "vitest";

import { BundleExportError, attachOkf, exportDuckDb } from "../src/index.js";

const connections: DuckDBConnection[] = [];

afterEach(() => {
  for (const connection of connections.splice(0)) connection.closeSync();
});

async function bundle(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-duckdb-"));
  await writeFile(
    path.join(root, "a.md"),
    "---\ntype: Node\nrelated: /b.md\n---\n[B](b.md)\n",
  );
  await writeFile(path.join(root, "b.md"), "---\ntype: Node\n---\n");
  return root;
}

async function count(connection: DuckDBConnection, table: string): Promise<number> {
  const reader = await connection.runAndReadAll(`SELECT count(*)::INTEGER FROM ${table}`);
  const value = reader.getRowsJS()[0]?.[0];
  if (typeof value !== "number") throw new TypeError(`unexpected count: ${String(value)}`);
  return value;
}

test("materializes ordinary queryable tables", async () => {
  const root = await bundle();
  const connection = await DuckDBConnection.create();
  connections.push(connection);

  const report = await attachOkf(connection, root);

  expect(report).toMatchObject({
    schema: "okf",
    conformant: true,
    markdown_count: 2,
    concept_count: 2,
    link_count: 1,
    diagnostic_count: 0,
  });
  await expect(count(connection, "okf.concepts")).resolves.toBe(2);
  await expect(count(connection, "okf.links")).resolves.toBe(1);
  await expect(count(connection, "okf.reserved")).resolves.toBe(0);
  await expect(count(connection, "okf.diagnostics")).resolves.toBe(0);
});

test("rejects unsafe schema names", async () => {
  const root = await bundle();
  const connection = await DuckDBConnection.create();
  connections.push(connection);

  await expect(
    attachOkf(connection, root, { schema: 'okf"; DROP TABLE x; --' }),
  ).rejects.toThrow("invalid DuckDB schema name");
});

test("refuses collisions and overwrites only when explicit", async () => {
  const root = await bundle();
  const connection = await DuckDBConnection.create();
  connections.push(connection);
  await attachOkf(connection, root);

  await expect(attachOkf(connection, root)).rejects.toMatchObject({
    name: "BundleExportError",
    schemaName: "okf",
    tables: expect.arrayContaining(["concepts"]),
  } satisfies Partial<BundleExportError>);

  await expect(attachOkf(connection, root, { overwrite: true })).resolves.toMatchObject({
    concept_count: 2,
  });
  await expect(count(connection, "okf.concepts")).resolves.toBe(2);
});

test("exports a persistent database file", async () => {
  const root = await bundle();
  const directory = await mkdtemp(path.join(tmpdir(), "okf-parser-duckdb-file-"));
  const database = path.join(directory, "bundle.duckdb");

  const report = await exportDuckDb(root, { database });

  expect(report.database).toBe(path.resolve(database));
  const instance = await DuckDBInstance.create(database);
  const connection = await instance.connect();
  connections.push(connection);
  await expect(count(connection, "okf.concepts")).resolves.toBe(2);
});
