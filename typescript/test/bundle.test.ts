import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import { checkBundle, discoverMarkdown, graphBundle, inventoryBundle } from "../src/index.js";

async function bundle(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-ts-"));
  await writeFile(path.join(root, "index.md"), "---\nokf_version: 0.2\n---\n# Knowledge\n");
  await writeFile(
    path.join(root, "one.md"),
    "---\ntype: Reference\ntitle: One\nrelated: two.md\n---\n# One\n[Two](two.md)\n",
  );
  await writeFile(path.join(root, "two.md"), "---\ntype: Reference\ntitle: Two\n---\n# Two\n");
  return root;
}

test("an absent .okfignore is the ordinary unfiltered case", async () => {
  const root = await bundle();

  await expect(discoverMarkdown(root)).resolves.toHaveLength(3);
});

test("validates, inventories, and projects the graph", async () => {
  const root = await bundle();
  const report = await checkBundle(root);
  expect(report).toMatchObject({
    conformant: true,
    markdown_count: 3,
    concept_count: 2,
    reserved_count: 1,
    diagnostics: [],
  });
  expect(await inventoryBundle(root)).toMatchObject({
    types: [{ concept_type: "Reference", concept_count: 2 }],
  });
  expect(await graphBundle(root)).toMatchObject({
    nodes: 2,
    edges: 2,
    weakly_connected_components: 1,
    strongly_connected_components: 2,
    directed_acyclic: true,
  });
});
