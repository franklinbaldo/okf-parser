import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import { ExclusionRules, checkBundle, discoverMarkdown } from "../src/core.js";

interface PathCase {
  readonly path: string;
  readonly is_dir?: boolean;
  readonly excluded: boolean;
}

interface ExclusionCase {
  readonly name: string;
  readonly patterns: readonly string[];
  readonly paths: readonly PathCase[];
}

const cases = JSON.parse(
  await readFile(new URL("../../conformance/exclusion.json", import.meta.url), "utf8"),
) as readonly ExclusionCase[];

async function write(root: string, relative: string, content: string): Promise<void> {
  const target = path.join(root, relative);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, content, "utf8");
}

test.each(cases)("matching follows shared conformance: $name", ({ patterns, paths }) => {
  const rules = new ExclusionRules(patterns);
  for (const item of paths) {
    expect(rules.excludes(item.path, { isDir: item.is_dir ?? false }), item.path).toBe(
      item.excluded,
    );
  }
});

test("a negation is visible to the walk", () => {
  expect(new ExclusionRules(["vendor"]).hasNegation).toBe(false);
  expect(new ExclusionRules(["vendor", "!vendor/knowledge"]).hasNegation).toBe(true);
});

test("a negation re-includes knowledge inside an excluded directory", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-exclusion-"));
  await write(root, "vendor/dep/guide.md", "# Guide\n");
  await write(root, "vendor/knowledge/tarefa.md", "---\ntype: Work Item\n---\n\n# Tarefa\n");
  await write(root, ".okfignore", "vendor\n!vendor/knowledge\n");

  const found = await discoverMarkdown(root);
  const report = await checkBundle(root);

  expect(found.map((item) => path.relative(root, item))).toEqual([
    path.join("vendor", "knowledge", "tarefa.md"),
  ]);
  expect(report.conformant).toBe(true);
  expect(report.concept_count).toBe(1);
});

test("an excluded directory is still pruned without any negation", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-exclusion-"));
  await write(root, "vendor/dep/guide.md", "# Guide\n");
  await write(root, "items/tarefa.md", "---\ntype: Work Item\n---\n\n# Tarefa\n");
  await write(root, ".okfignore", "vendor\n");

  const found = await discoverMarkdown(root);

  expect(found.map((item) => path.relative(root, item))).toEqual([
    path.join("items", "tarefa.md"),
  ]);
});
