import { mkdtemp, readFile, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import {
  canonicalizeSimpleFrontmatterBlock,
  formatMarkdown,
  formatPath,
  protectedBlockSignature,
  safeFormatMarkdown,
} from "../src/formatting.js";

interface FormattingCase {
  readonly name: string;
  readonly source: string;
  readonly expect_change: boolean | null;
  readonly ordered_lists: number;
  readonly bullet_lists: number;
  readonly required: readonly string[];
  readonly forbidden: readonly string[];
}

const cases = JSON.parse(
  await readFile(new URL("../../conformance/formatting.json", import.meta.url), "utf8"),
) as readonly FormattingCase[];

test.each(cases)("shared formatting contract: $name", (item) => {
  const formatted = formatMarkdown(item.source);

  expect(safeFormatMarkdown(item.source)).toBe(formatted);
  expect(protectedBlockSignature(formatted)).toEqual(protectedBlockSignature(item.source));
  expect(formatMarkdown(formatted)).toBe(formatted);
  if (item.expect_change !== null) expect(formatted !== item.source).toBe(item.expect_change);
  for (const required of item.required) expect(formatted).toContain(required);
  for (const forbidden of item.forbidden) expect(formatted).not.toContain(forbidden);

  const lists = protectedBlockSignature(formatted).filter((block) => block.type === "list");
  expect(lists.filter((block) => block.attributes.ordered === true)).toHaveLength(
    item.ordered_lists,
  );
  expect(lists.filter((block) => block.attributes.ordered === false)).toHaveLength(
    item.bullet_lists,
  );
});

test("simple frontmatter uses the shared physical order", () => {
  const source =
    "status: active\ndescription: Plain text\ntype: Reference\nnumber: 0012\ntitle: Example\nactive: false\n";

  expect(canonicalizeSimpleFrontmatterBlock(source)).toBe(
    "type: Reference\ntitle: Example\ndescription: Plain text\nactive: false\nnumber: 0012\nstatus: active\n",
  );
});

test("complex frontmatter remains byte-for-byte on the generic path", () => {
  for (const source of [
    "type: Reference # keep me\ntitle: Example",
    "type: Reference\nitems:\n  - one\n  - two",
    "type: 'Reference'\ntitle: Example",
    "type: Reference\ntitle: Example: subtitle",
  ]) {
    expect(canonicalizeSimpleFrontmatterBlock(source)).toBe(source);
  }
});

test("formatPath writes canonical simple frontmatter order", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-format-frontmatter-"));
  const markdownPath = path.join(root, "concept.md");
  await writeFile(
    markdownPath,
    "---\nstatus: active\ndescription: Plain text\ntype: Reference\nnumber: 0012\ntitle: Example\nactive: false\n---\n# Example\n",
    "utf8",
  );

  const report = await formatPath(root, { write: true });
  const written = await readFile(markdownPath, "utf8");

  expect(report.succeeded).toBe(true);
  expect(written.startsWith(
    "---\ntype: Reference\ntitle: Example\ndescription: Plain text\nactive: false\nnumber: 0012\nstatus: active\n---\n",
  )).toBe(true);
});

test("check is read-only and write is explicit", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-format-"));
  const markdownPath = path.join(root, "concept.md");
  const original = "# Heading\n\n-   item\n";
  await writeFile(markdownPath, original, "utf8");

  const check = await formatPath(root);

  expect(check).toMatchObject({
    markdown_count: 1,
    changed_paths: ["concept.md"],
    skipped_paths: [],
    written: false,
    clean: false,
    succeeded: false,
  });
  expect(await readFile(markdownPath, "utf8")).toBe(original);

  const write = await formatPath(root, { write: true });

  expect(write.written).toBe(true);
  expect(write.succeeded).toBe(true);
  expect(await readFile(markdownPath, "utf8")).toBe("# Heading\n\n- item\n");
});

test("unreadable UTF-8 is skipped and prevents success", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-format-"));
  await writeFile(path.join(root, "latin1.md"), Buffer.from([0x23, 0x20, 0x43, 0x61, 0x66, 0xe9]));

  const report = await formatPath(root, { write: true });

  expect(report).toMatchObject({
    markdown_count: 1,
    changed_paths: [],
    skipped_paths: ["latin1.md"],
    written: true,
    clean: false,
    succeeded: false,
  });
});

test("ignored dependencies are neither read nor written", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-format-"));
  const dependency = path.join(root, "node_modules", "package", "README.md");
  await import("node:fs/promises").then(({ mkdir }) => mkdir(path.dirname(dependency), { recursive: true }));
  const original = "# Dependency\n\n-   leave alone\n";
  await writeFile(dependency, original, "utf8");

  const report = await formatPath(root, { write: true });

  expect(report.markdown_count).toBe(0);
  expect(await readFile(dependency, "utf8")).toBe(original);
});

test("Markdown symlinks are not followed", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-format-"));
  const outside = path.join(tmpdir(), `outside-${path.basename(root)}.md`);
  const link = path.join(root, "linked.md");
  const original = "# Outside\n\n-   leave alone\n";
  await writeFile(outside, original, "utf8");
  try {
    await symlink(outside, link);
  } catch {
    return;
  }

  const report = await formatPath(root, { write: true });

  expect(report.markdown_count).toBe(0);
  expect(await readFile(outside, "utf8")).toBe(original);
});
