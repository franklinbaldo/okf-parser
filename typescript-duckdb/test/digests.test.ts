import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { DuckDBConnection } from "@duckdb/node-api";
import { expect, test } from "vitest";

import { attachOkf } from "../src/index.js";

test("materialized concepts expose self-describing content digests", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-digests-"));
  await writeFile(path.join(root, "note.md"), "---\ntype: Note\n---\nBody\n");
  const connection = await DuckDBConnection.create();
  try {
    await attachOkf(connection, root);
    const reader = await connection.runAndReadAll(
      "SELECT source_digest, parsed_digest FROM okf.concepts",
    );
    const [source, parsed] = reader.getRowsJS()[0] ?? [];
    expect(String(source)).toMatch(/^sha256:[0-9a-f]{64}$/u);
    expect(String(parsed)).toMatch(/^okf-parsed-v1-jcs-sha256:[0-9a-f]{64}$/u);
  } finally {
    connection.closeSync();
  }
});
