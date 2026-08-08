import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import {
  SchemaNameCollisionError,
  compileTypeContracts,
  exportJsonSchema,
  exportZod,
} from "../src/index.js";

async function writeConcept(root: string, name: string, frontmatter: string): Promise<void> {
  await writeFile(path.join(root, name), `---\n${frontmatter}---\nBody\n`);
}

test("keeps requiredness, nullability, and list item nullability independent", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-schema-ts-"));
  await writeConcept(
    root,
    "one.md",
    "type: test_type\noptional_value: present\nnullable_value: null\nvalues: [null, 1]\n",
  );
  await writeConcept(
    root,
    "two.md",
    "type: test_type\nnullable_value: present\nvalues: [2]\n",
  );
  const report = await exportJsonSchema(root, { inferTypes: true });
  const schema = report.schemas.test_type as {
    readonly properties: Readonly<Record<string, Record<string, unknown>>>;
    readonly required: readonly string[];
  };
  expect(schema.required).not.toContain("optional_value");
  expect(schema.properties.optional_value?.type).toBe("string");
  expect(schema.required).toContain("nullable_value");
  expect(schema.properties.nullable_value?.anyOf).toEqual([
    { type: "string" },
    { type: "null" },
  ]);
  expect(schema.properties.values?.items).toEqual({
    anyOf: [{ type: "integer" }, { type: "null" }],
  });
  const zod = await exportZod(root, { inferTypes: true });
  expect(zod).toContain('"optional_value": z.string().optional()');
  expect(zod).toContain('"nullable_value": z.string().nullable()');
  expect(zod).toContain('"values": z.array(z.number().int().nullable())');
});

test("preserves Unicode names and rejects normalized collisions", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-names-ts-"));
  await writeConcept(root, "accent.md", "type: ação\n");
  await writeConcept(root, "japanese.md", "type: 日本\n");
  const zod = await exportZod(root);
  expect(zod).toContain("export const AçãoSchema =");
  expect(zod).toContain("export const 日本Schema =");

  const collision = await mkdtemp(path.join(tmpdir(), "okf-collision-ts-"));
  await writeConcept(collision, "hyphen.md", "type: a-o\n");
  await writeConcept(collision, "underscore.md", "type: a_o\n");
  await expect(exportJsonSchema(collision)).rejects.toBeInstanceOf(SchemaNameCollisionError);
});


test("exposes one immutable deterministic TypeContract per authored type", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-contract-ts-"));
  await writeConcept(root, "one.md", "type: Note\nrank: 1\n");
  await writeConcept(root, "two.md", "type: Note\n");

  const contracts = await compileTypeContracts(root, { inferTypes: true });

  expect(Object.isFrozen(contracts)).toBe(true);
  expect(contracts).toHaveLength(1);
  expect(contracts[0]?.conceptType).toBe("Note");
  expect(contracts[0]?.modelName).toBe("NoteConcept");
  expect(contracts[0]?.root.fields).toEqual(expect.arrayContaining([
    expect.objectContaining({ name: "type", required: true, nullable: false }),
    expect.objectContaining({ name: "rank", required: false, nullable: false }),
  ]));
});
