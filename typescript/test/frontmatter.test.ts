import { readFile } from "node:fs/promises";
import path from "node:path";

import { describe, expect, test } from "vitest";

import { parseDocumentContent } from "../src/index.js";

interface ConformanceCase {
  readonly name: string;
  readonly source: string;
  readonly frontmatter: Readonly<Record<string, unknown>>;
  readonly body: string;
}

describe("shared frontmatter conformance", () => {
  test("matches every language-neutral case", async () => {
    const fixture = path.resolve(import.meta.dirname, "../../conformance/frontmatter.json");
    const cases = JSON.parse(await readFile(fixture, "utf8")) as readonly ConformanceCase[];
    for (const item of cases) {
      const parsed = parseDocumentContent(item.source, item.name);
      expect(parsed.frontmatter, item.name).toEqual(item.frontmatter);
      expect(parsed.body, item.name).toBe(item.body);
    }
  });
});

test("rejects cyclic aliases instead of recursing forever", () => {
  expect(() =>
    parseDocumentContent("---\ntype: Reference\nself: &a\n  child: *a\n---\n", "cyclic.md"),
  ).toThrow(/cyclic YAML anchor/u);
});

test("linear splitter preserves CRLF bodies and ignores delimiter-like content", () => {
  const parsed = parseDocumentContent(
    "---\r\ntype: Reference\r\nnote: before --- after\r\n---\t \r\n# Body\r\n---\r\n",
    "crlf.md",
  );
  expect(parsed.frontmatter.note).toBe("before --- after");
  expect(parsed.body).toBe("# Body\r\n---\r\n");
});

test("frontmatter delimiter must be an isolated line", () => {
  expect(() =>
    parseDocumentContent("---\ntype: Reference\n--- nope\n# Body\n", "invalid.md"),
  ).toThrow(/frontmatter delimited/u);
});
