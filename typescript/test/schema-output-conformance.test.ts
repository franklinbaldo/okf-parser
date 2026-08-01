import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import { exportJsonSchema, exportZod } from "../src/index.js";

interface DocumentCase {
  readonly name: string;
  readonly content: string;
}

interface OutputCase {
  readonly id: string;
  readonly documents: readonly DocumentCase[];
  readonly options: {
    readonly infer_types: boolean;
    readonly casts: readonly string[];
  };
  readonly schemas: Readonly<Record<string, unknown>>;
  readonly zod: string;
}

interface Fixture {
  readonly cases: readonly OutputCase[];
}

const fixture = JSON.parse(
  await readFile(new URL("../../conformance/schema-output.json", import.meta.url), "utf8"),
) as Fixture;

for (const outputCase of fixture.cases) {
  test(`matches canonical schema output: ${outputCase.id}`, async () => {
    const root = await mkdtemp(path.join(tmpdir(), `okf-schema-output-${outputCase.id}-`));
    await Promise.all(
      outputCase.documents.map((document) =>
        writeFile(path.join(root, document.name), document.content, "utf8"),
      ),
    );
    const options = {
      inferTypes: outputCase.options.infer_types,
      casts: outputCase.options.casts,
    };

    const report = await exportJsonSchema(root, options);

    expect(report.schemas).toEqual(outputCase.schemas);
    expect(report.total_types).toBe(Object.keys(outputCase.schemas).length);
    expect(report.inferred_types).toBe(outputCase.options.infer_types);
    expect(report.casts).toEqual(outputCase.options.casts);
    await expect(exportZod(root, options)).resolves.toBe(outputCase.zod);
  });
}

test("Astro mode changes only the import", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-schema-output-astro-"));
  await writeFile(path.join(root, "sample.md"), "---\ntype: sample\n---\nBody\n", "utf8");

  const generic = await exportZod(root);
  const astro = await exportZod(root, { zodImport: "astro" });

  expect(generic).toContain("import { z } from 'zod';");
  expect(astro).toBe(generic.replace("from 'zod'", "from 'astro:content'"));
});
