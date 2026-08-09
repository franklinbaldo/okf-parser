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

test("exposes one deeply immutable deterministic TypeContract per authored type", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-contract-ts-"));
  await writeConcept(root, "one.md", "type: Note\nrank: 1\nvalues: [1, 2]\n");
  await writeConcept(root, "two.md", "type: Note\nvalues: [3]\n");

  const contracts = await compileTypeContracts(root, { inferTypes: true });
  const contract = contracts[0];
  const rank = contract?.root.fields.find((field) => field.name === "rank");
  const values = contract?.root.fields.find((field) => field.name === "values");

  expect(Object.isFrozen(contracts)).toBe(true);
  expect(Object.isFrozen(contract)).toBe(true);
  expect(Object.isFrozen(contract?.root)).toBe(true);
  expect(Object.isFrozen(contract?.root.fields)).toBe(true);
  expect(Object.isFrozen(rank)).toBe(true);
  expect(Object.isFrozen(rank?.value)).toBe(true);
  expect(Object.isFrozen(values)).toBe(true);
  expect(Object.isFrozen(values?.value)).toBe(true);
  if (values?.value.kind === "list") expect(Object.isFrozen(values.value.item)).toBe(true);

  expect(contracts).toHaveLength(1);
  expect(contract?.conceptType).toBe("Note");
  expect(contract?.modelName).toBe("NoteConcept");
  expect(contract?.root.fields).toEqual(expect.arrayContaining([
    expect.objectContaining({ name: "type", required: true, nullable: false }),
    expect.objectContaining({ name: "rank", required: false, nullable: false }),
  ]));
});

test("preserves declared DuckDB identity in TypeContract, JSON Schema, and Zod", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-declared-contract-ts-"));
  await writeConcept(root, "one.md", "type: Registro\nvalor: '12.34'\nsequencia: '9007199254740993'\n");
  const declaredTypesByType = {
    Registro: {
      valor: { sql: "DECIMAL(18,4)", family: "decimal", precision: 18, scale: 4 },
      sequencia: { sql: "BIGINT", family: "integer", isJsSafeInteger: false },
      criado_em: { sql: "TIMESTAMP", family: "timestamp" },
    },
  } as const;

  const contracts = await compileTypeContracts(root, { declaredTypesByType });
  const fields = contracts[0]?.root.fields ?? [];
  const valor = fields.find((field) => field.name === "valor")?.value;
  const criadoEm = fields.find((field) => field.name === "criado_em")?.value;
  expect(valor).toMatchObject({
    declaredType: { sql: "DECIMAL(18,4)", family: "decimal" },
  });
  expect(Object.isFrozen(valor)).toBe(true);
  if (valor?.kind === "scalar") expect(Object.isFrozen(valor.declaredType)).toBe(true);
  expect(criadoEm).toMatchObject({
    kind: "scalar",
    scalar: "datetime",
    declaredType: { sql: "TIMESTAMP", family: "timestamp" },
  });
  expect(fields.find((field) => field.name === "criado_em")?.required).toBe(false);

  const schema = await exportJsonSchema(root, { declaredTypesByType });
  const properties = (schema.schemas.Registro as { properties: Record<string, Record<string, unknown>> }).properties;
  expect(properties.valor).toMatchObject({ type: "number", multipleOf: 0.0001, "x-okf-duckdb-type": "DECIMAL(18,4)" });
  expect(properties.criado_em).toMatchObject({ type: "string", "x-okf-temporal-kind": "timestamp-without-time-zone" });

  const zod = await exportZod(root, { declaredTypesByType });
  expect(zod).toContain('"valor": z.string()');
  expect(zod).toContain('"sequencia": z.union([z.number().int(), z.bigint()])');
});
