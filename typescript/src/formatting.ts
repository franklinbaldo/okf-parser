import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";

import type { Node, Parent } from "unist";
import { frontmatterFromMarkdown, frontmatterToMarkdown } from "mdast-util-frontmatter";
import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown, gfmToMarkdown } from "mdast-util-gfm";
import { toMarkdown } from "mdast-util-to-markdown";
import { frontmatter } from "micromark-extension-frontmatter";
import { gfm } from "micromark-extension-gfm";

import { ExclusionRules, discoverMarkdown } from "./core.js";

const BLOCK_CONTAINERS = new Set([
  "root",
  "blockquote",
  "list",
  "listItem",
  "table",
  "tableRow",
  "tableCell",
  "footnoteDefinition",
]);
const LEAF_BLOCKS = new Set([
  "yaml",
  "code",
  "heading",
  "html",
  "paragraph",
  "thematicBreak",
  "definition",
]);

export type ProtectedAttribute = string | number | boolean | null | readonly string[];

export interface ProtectedBlockRecord {
  readonly type: string;
  readonly depth: number;
  readonly attributes: Readonly<Record<string, ProtectedAttribute>>;
  readonly content?: string;
}

export interface FormatOptions {
  readonly exclude?: readonly string[];
  readonly signal?: AbortSignal;
  readonly write?: boolean;
}

export interface FormatReport {
  readonly markdown_count: number;
  readonly changed_paths: readonly string[];
  readonly skipped_paths: readonly string[];
  readonly written: boolean;
  readonly clean: boolean;
  readonly succeeded: boolean;
}

function parseMarkdown(text: string) {
  return fromMarkdown(text, {
    extensions: [frontmatter(["yaml"]), gfm()],
    mdastExtensions: [frontmatterFromMarkdown(["yaml"]), gfmFromMarkdown()],
  });
}

function isParent(node: Node): node is Parent {
  return "children" in node && Array.isArray(node.children);
}

function recordOf(node: Node): Readonly<Record<string, unknown>> {
  return node as Node & Readonly<Record<string, unknown>>;
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function nullableBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function stringArray(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return Object.freeze([]);
  return Object.freeze(value.map((item) => (typeof item === "string" ? item : "")));
}

function protectedAttributes(node: Node): Readonly<Record<string, ProtectedAttribute>> {
  const record = recordOf(node);
  switch (node.type) {
    case "heading":
      return Object.freeze({ depth: nullableNumber(record.depth) });
    case "list":
      return Object.freeze({
        ordered: nullableBoolean(record.ordered),
        start: nullableNumber(record.start),
        spread: nullableBoolean(record.spread),
      });
    case "listItem":
      return Object.freeze({
        spread: nullableBoolean(record.spread),
        checked: nullableBoolean(record.checked),
      });
    case "code":
      return Object.freeze({
        lang: nullableString(record.lang),
        meta: nullableString(record.meta),
      });
    case "table":
      return Object.freeze({ align: stringArray(record.align) });
    case "definition":
      return Object.freeze({
        identifier: nullableString(record.identifier),
        label: nullableString(record.label),
        url: nullableString(record.url),
        title: nullableString(record.title),
      });
    case "footnoteDefinition":
      return Object.freeze({ identifier: nullableString(record.identifier) });
    default:
      return Object.freeze({});
  }
}

function protectedContent(node: Node): string | undefined {
  if (node.type !== "yaml" && node.type !== "code" && node.type !== "html") return undefined;
  const value = recordOf(node).value;
  return typeof value === "string" ? value : "";
}

function collectProtectedBlocks(
  node: Node,
  depth: number,
  output: ProtectedBlockRecord[],
): void {
  if (node.type !== "root" && (BLOCK_CONTAINERS.has(node.type) || LEAF_BLOCKS.has(node.type))) {
    const content = protectedContent(node);
    output.push(
      Object.freeze({
        type: node.type,
        depth,
        attributes: protectedAttributes(node),
        ...(content === undefined ? {} : { content }),
      }),
    );
  }
  if (!BLOCK_CONTAINERS.has(node.type) || !isParent(node)) return;
  for (const child of node.children) collectProtectedBlocks(child, depth + 1, output);
}

export function protectedBlockSignature(text: string): readonly ProtectedBlockRecord[] {
  const output: ProtectedBlockRecord[] = [];
  collectProtectedBlocks(parseMarkdown(text), 0, output);
  return Object.freeze(output);
}

export function formatMarkdown(text: string): string {
  return toMarkdown(parseMarkdown(text), {
    extensions: [frontmatterToMarkdown(["yaml"]), gfmToMarkdown()],
    bullet: "-",
    bulletOther: "*",
    bulletOrdered: ".",
    emphasis: "*",
    fence: "`",
    fences: true,
    incrementListMarker: true,
    listItemIndent: "one",
    rule: "-",
  });
}

export function safeFormatMarkdown(text: string): string | null {
  const formatted = formatMarkdown(text);
  return isDeepStrictEqual(protectedBlockSignature(formatted), protectedBlockSignature(text))
    ? formatted
    : null;
}

async function readUtf8(filePath: string): Promise<string> {
  const bytes = await readFile(filePath);
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

export async function formatPath(
  input: string | URL,
  options: FormatOptions = {},
): Promise<FormatReport> {
  const root = path.resolve(input instanceof URL ? input.pathname : input);
  const exclusions = await ExclusionRules.read(root, options.exclude ?? []);
  const paths = await discoverMarkdown(root, exclusions, options.signal);
  const changedPaths: string[] = [];
  const skippedPaths: string[] = [];
  const pending: Array<readonly [string, string]> = [];

  for (const markdownPath of paths) {
    options.signal?.throwIfAborted();
    const relative = path.relative(root, markdownPath).split(path.sep).join("/");
    let original: string;
    try {
      original = await readUtf8(markdownPath);
    } catch {
      skippedPaths.push(relative);
      continue;
    }
    const formatted = safeFormatMarkdown(original);
    if (formatted === null) {
      skippedPaths.push(relative);
      continue;
    }
    if (formatted === original) continue;
    changedPaths.push(relative);
    pending.push([markdownPath, formatted]);
  }

  const written = options.write === true;
  if (written) {
    for (const [markdownPath, formatted] of pending) {
      options.signal?.throwIfAborted();
      await writeFile(markdownPath, formatted, "utf8");
    }
  }

  const clean = changedPaths.length === 0 && skippedPaths.length === 0;
  const succeeded = skippedPaths.length === 0 && (written || changedPaths.length === 0);
  return Object.freeze({
    markdown_count: paths.length,
    changed_paths: Object.freeze(changedPaths),
    skipped_paths: Object.freeze(skippedPaths),
    written,
    clean,
    succeeded,
  });
}
