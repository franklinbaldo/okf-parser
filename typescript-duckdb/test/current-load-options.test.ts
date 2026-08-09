import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { DuckDBConnection } from "@duckdb/node-api";
import { expect, test } from "vitest";

import { attachOkf, openOkfSession } from "../src/index.js";

async function bundle(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "okf-current-options-"));
  await writeFile(path.join(root, "one.md"), "---\ntype: Note\n---\n# One\n");
  return root;
}

test("attachOkf preserves current load options", async () => {
  const root = await bundle();
  const connection = await DuckDBConnection.create();
  try {
    await expect(
      attachOkf(connection, root, {
        readConcurrency: 1,
        rustCore: path.join(root, "missing-okf-core"),
      }),
    ).rejects.toThrow();
  } finally {
    connection.closeSync();
  }
});

test("openOkfSession preserves current load options", async () => {
  const root = await bundle();
  await expect(
    openOkfSession(root, {
      readConcurrency: 1,
      rustCore: path.join(root, "missing-okf-core"),
    }),
  ).rejects.toThrow();
});
