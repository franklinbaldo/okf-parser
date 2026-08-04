import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import { checkBundle } from "../src/core.js";
import { specRelativePath, typeSlug } from "../src/type-specs.js";

interface SlugCase {
  readonly name: string;
  readonly concept_type: string;
  readonly slug: string;
}

const TEMPLATE = ".okf/specs/{slug}.md";
const cases = JSON.parse(
  await readFile(new URL("../../conformance/type-spec-slugs.json", import.meta.url), "utf8"),
) as readonly SlugCase[];

async function bundle(concepts: Readonly<Record<string, string>>): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "okf-type-specs-"));
  for (const [relative, conceptType] of Object.entries(concepts)) {
    const target = path.join(root, relative);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, `---\ntype: ${conceptType}\n---\n\n# ${conceptType}\n`, "utf8");
  }
  return root;
}

test.each(cases)("type slug matches shared conformance: $name", ({ concept_type, slug }) => {
  expect(typeSlug(concept_type)).toBe(slug);
});

test("specRelativePath requires the slug placeholder", () => {
  expect(() => specRelativePath(".okf/specs/spec.md", "Spec")).toThrow(/must contain/u);
});

test("specRelativePath reports a type without a slug", () => {
  expect(specRelativePath(TEMPLATE, "概念")).toBeNull();
});

test("check is unchanged without the rule", async () => {
  const root = await bundle({ "a.md": "Revisão Ciência" });
  expect((await checkBundle(root)).diagnostics).toEqual([]);
});

test("a missing specification is advisory", async () => {
  const root = await bundle({ "a.md": "Revisão Ciência" });
  const report = await checkBundle(root, { requireSpec: TEMPLATE });
  expect(report.conformant).toBe(true);
  expect(report.diagnostics).toEqual([
    {
      code: "OKF010",
      severity: "warning",
      path: ".okf/specs/revisao-ciencia.md",
      message: 'type "Revisão Ciência" has no specification document',
    },
  ]);
});

test("a missing specification is normative under the flag", async () => {
  const root = await bundle({ "a.md": "Revisão Ciência" });
  const report = await checkBundle(root, { requireSpec: TEMPLATE, normativeSpec: true });
  expect(report.conformant).toBe(false);
  expect(report.diagnostics[0]?.severity).toBe("error");
});

test("a present specification produces no diagnostic", async () => {
  const root = await bundle({
    "a.md": "Revisão Ciência",
    ".okf/specs/revisao-ciencia.md": "Spec",
    ".okf/specs/spec.md": "Spec",
  });
  expect((await checkBundle(root, { requireSpec: TEMPLATE })).diagnostics).toEqual([]);
});

test("each type is reported once", async () => {
  const root = await bundle({
    "a0.md": "Revisão Ciência",
    "a1.md": "Revisão Ciência",
    "a2.md": "Revisão Ciência",
  });
  expect((await checkBundle(root, { requireSpec: TEMPLATE })).diagnostics).toHaveLength(1);
});

test("a type without a slug is reported against the template", async () => {
  const root = await bundle({ "a.md": "概念" });
  const report = await checkBundle(root, { requireSpec: TEMPLATE });
  expect(report.diagnostics[0]?.path).toBe(TEMPLATE);
});
