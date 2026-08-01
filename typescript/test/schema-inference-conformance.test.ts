import { readFile } from "node:fs/promises";

import { expect, test } from "vitest";

import { canClassifyAs, classifyLexemes, type LexemeKind } from "../src/lexemes.js";

interface ClassificationCase {
  readonly id: string;
  readonly values: readonly string[];
  readonly kind: LexemeKind;
}

interface CastCase extends ClassificationCase {
  readonly valid: boolean;
}

interface Fixture {
  readonly classification: readonly ClassificationCase[];
  readonly casts: readonly CastCase[];
}

const fixture = JSON.parse(
  await readFile(new URL("../../conformance/schema-inference.json", import.meta.url), "utf8"),
) as Fixture;

test.each(fixture.classification)("classifies $id", ({ values, kind }) => {
  expect(classifyLexemes(values)).toBe(kind);
});

test.each(fixture.casts)("validates cast $id", ({ values, kind, valid }) => {
  expect(canClassifyAs(values, kind)).toBe(valid);
});
