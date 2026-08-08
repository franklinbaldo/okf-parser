import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import {
  loadDeclaredSchemas,
  materializeTypedBundle,
  openOkfSession,
} from "../src/index.js";

async function fixture(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "okf-typed-session-"));
  await writeFile(
    path.join(root, "one.md"),
    `---
type: Registro
amount: '12.3400'
amounts: ['1.2500', '2.5000']
big: '9007199254740993'
day: '2026-08-08'
local_at: '2026-08-08 12:30:00'
zoned_at: '2026-08-08T12:30:00+00:00'
external_id: 550e8400-e29b-41d4-a716-446655440000
extra: kept
structured: [not, a, scalar]
---
First body
`,
  );
  await writeFile(
    path.join(root, "two.md"),
    `---
type: Registro
amount: not-a-decimal
big: not-an-integer
---
Second body
`,
  );
  const types = path.join(root, "docs", "types");
  await mkdir(types, { recursive: true });
  await writeFile(
    path.join(types, "registro.schema.sql"),
    `CREATE TABLE "Registro" (
      amount DECIMAL(18,4),
      amounts DECIMAL(18,4)[],
      big BIGINT,
      day DATE,
      local_at TIMESTAMP,
      zoned_at TIMESTAMPTZ,
      external_id UUID
    );
    COMMENT ON TABLE "Registro" IS 'Typed records.';
    COMMENT ON COLUMN "Registro".amount IS 'Declared amount.';
`,
  );
  return root;
}

test("opens one owned session with normalized and typed relations", async () => {
  const root = await fixture();
  const session = await openOkfSession(root, { specTemplate: "docs/types/{slug}.md" });
  try {
    expect(session.normalized.concept_count).toBe(2);
    expect(session.typed).toMatchObject({ schema: "okf_types", tables: ["Registro"] });

    const normalized = await session.connection.runAndReadAll("SELECT count(*)::INTEGER FROM okf.concepts");
    expect(normalized.getRowsJS()[0]?.[0]).toBe(2);

    const valid = await session.connection.runAndReadAll(
      `SELECT __okf_raw_amount, amount::VARCHAR, big::VARCHAR, day::VARCHAR,
              local_at::VARCHAR, zoned_at::VARCHAR, external_id::VARCHAR, extra
       FROM okf_types."Registro"
       WHERE __okf_concept_id = $id`,
      { id: "one" },
    );
    const row = valid.getRowsJS()[0];
    expect(row?.[0]).toBe("12.3400");
    expect(row?.[1]).toBe("12.3400");
    expect(row?.[2]).toBe("9007199254740993");
    expect(row?.[3]).toBe("2026-08-08");
    expect(row?.[4]).toBe("2026-08-08 12:30:00");
    expect(row?.[6]).toBe("550e8400-e29b-41d4-a716-446655440000");
    expect(row?.[7]).toBe("kept");

    const invalid = await session.connection.runAndReadAll(
      `SELECT __okf_raw_amount, amount, __okf_raw_big, big
       FROM okf_types."Registro" WHERE __okf_concept_id = $id`,
      { id: "two" },
    );
    expect(invalid.getRowsJS()[0]).toEqual(["not-a-decimal", null, "not-an-integer", null]);

    const array = await session.connection.runAndReadAll(
      `SELECT __okf_raw_amounts::VARCHAR, amounts::VARCHAR
       FROM okf_types."Registro" WHERE __okf_concept_id = 'one'`,
    );
    expect(String(array.getRowsJS()[0]?.[0])).toContain("1.2500");
    expect(String(array.getRowsJS()[0]?.[1])).toContain("1.2500");

    const columns = await session.connection.runAndReadAll(
      `SELECT column_name FROM duckdb_columns()
       WHERE schema_name = 'okf_types' AND table_name = 'Registro'
       ORDER BY column_index`,
    );
    const names = columns.getRowsJS().map((item) => item[0]);
    expect(names).toContain("extra");
    expect(names).not.toContain("structured");
    expect(names).toContain("__okf_raw_amount");
    expect(names).toContain("amount");

    const comments = await session.connection.runAndReadAll(
      `SELECT
         (SELECT comment FROM duckdb_tables() WHERE schema_name = 'okf_types' AND table_name = 'Registro'),
         (SELECT comment FROM duckdb_columns() WHERE schema_name = 'okf_types' AND table_name = 'Registro' AND column_name = 'amount')`,
    );
    expect(comments.getRowsJS()[0]).toEqual(["Typed records.", "Declared amount."]);
  } finally {
    session.close();
  }
});

test("preserves existing catalog comments when an overwrite declaration omits them", async () => {
  const root = await fixture();
  const session = await openOkfSession(root, { specTemplate: "docs/types/{slug}.md" });
  try {
    await writeFile(
      path.join(root, "docs", "types", "registro.schema.sql"),
      `CREATE TABLE "Registro" (
        amount DECIMAL(18,4),
        amounts DECIMAL(18,4)[],
        big BIGINT,
        day DATE,
        local_at TIMESTAMP,
        zoned_at TIMESTAMPTZ,
        external_id UUID
      );
`,
    );
    const declarations = await loadDeclaredSchemas(
      root,
      ["Registro"],
      "docs/types/{slug}.md",
    );
    await materializeTypedBundle(session.connection, session.bundle, declarations, {
      overwrite: true,
    });

    const comments = await session.connection.runAndReadAll(
      `SELECT
         (SELECT comment FROM duckdb_tables() WHERE schema_name = 'okf_types' AND table_name = 'Registro'),
         (SELECT comment FROM duckdb_columns() WHERE schema_name = 'okf_types' AND table_name = 'Registro' AND column_name = 'amount')`,
    );
    expect(comments.getRowsJS()[0]).toEqual(["Typed records.", "Declared amount."]);
  } finally {
    session.close();
  }
});

test("session materialization is a snapshot of the one loaded Bundle", async () => {
  const root = await fixture();
  const session = await openOkfSession(root, { specTemplate: "docs/types/{slug}.md" });
  try {
    await writeFile(path.join(root, "three.md"), "---\ntype: Registro\namount: '99.0000'\n---\nLater\n");
    const count = await session.connection.runAndReadAll(
      `SELECT count(*)::INTEGER FROM okf_types."Registro"`,
    );
    expect(count.getRowsJS()[0]?.[0]).toBe(2);
    expect(session.bundle.concepts).toHaveLength(2);
  } finally {
    session.close();
  }
});
