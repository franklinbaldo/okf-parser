import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import { readUtf8Ordered, type ReadUtf8Result } from "../src/core.js";
import {
  checkBundle,
  discoverMarkdown,
  graphBundle,
  inventoryBundle,
  loadBundle,
} from "../src/index.js";

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
  // `related: two.md` is producer metadata; only [Two](two.md) is a relation.
  expect(await graphBundle(root)).toMatchObject({
    nodes: 2,
    edges: 1,
    weakly_connected_components: 1,
    strongly_connected_components: 2,
    directed_acyclic: true,
  });
});

test("bounded read-ahead preserves path order and isolates decode failures", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-ts-read-ahead-"));
  const names = Array.from({ length: 70 }, (_, index) => `concept-${String(index).padStart(3, "0")}.md`);
  await Promise.all(
    names.map((name) => writeFile(path.join(root, name), "---\ntype: Benchmark\n---\n# Concept\n")),
  );
  await writeFile(path.join(root, "invalid.md"), Buffer.from([0xff]));

  const sequential = await loadBundle(root, { readConcurrency: 1 });
  const loaded = await loadBundle(root, { readConcurrency: 32 });

  expect(loaded.concepts.map((concept) => concept.path)).toEqual(names);
  expect(loaded.diagnostics).toMatchObject([
    { code: "OKF001", path: "invalid.md", severity: "error" },
  ]);
  expect(loaded).toEqual(sequential);
  await expect(loadBundle(root, { readConcurrency: 0 })).rejects.toMatchObject({
    code: "OKF_READ_CONCURRENCY",
  });
});

test("bounded read-ahead admits no work after cancellation", async () => {
  const controller = new AbortController();
  const admitted: string[] = [];
  let releaseFirst: (() => void) | undefined;
  const firstBlocked = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  const read = async (filePath: string): Promise<ReadUtf8Result> => {
    admitted.push(filePath);
    if (filePath === "a.md") await firstBlocked;
    return { filePath, text: "" };
  };
  const iterator = readUtf8Ordered(
    ["a.md", "b.md", "c.md"],
    1,
    controller.signal,
    read,
  );
  const pending = iterator.next();
  await Promise.resolve();
  expect(admitted).toEqual(["a.md"]);

  controller.abort();
  releaseFirst?.();

  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  expect(admitted).toEqual(["a.md"]);

  const alreadyAborted = new AbortController();
  alreadyAborted.abort();
  const neverAdmitted: string[] = [];
  const abortedIterator = readUtf8Ordered(
    ["d.md"],
    1,
    alreadyAborted.signal,
    async (filePath) => {
      neverAdmitted.push(filePath);
      return { filePath, text: "" };
    },
  );
  await expect(abortedIterator.next()).rejects.toMatchObject({ name: "AbortError" });
  expect(neverAdmitted).toEqual([]);
});
