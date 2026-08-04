import { readFile, readdir, lstat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import MarkdownIt from "markdown-it";

import { missingTypeSpecs } from "./type-specs.js";
import { parseDocument as parseYamlDocument } from "yaml";

export interface FrontmatterObject {
  readonly [key: string]: FrontmatterValue;
}

export interface FrontmatterArray extends ReadonlyArray<FrontmatterValue> {}

export type FrontmatterValue = string | null | FrontmatterArray | FrontmatterObject;

export interface JsonObject {
  readonly [key: string]: JsonValue;
}

export interface JsonArray extends ReadonlyArray<JsonValue> {}

export type JsonValue = string | number | boolean | null | JsonArray | JsonObject;

export type Severity = "error" | "warning";

export interface Diagnostic {
  readonly code: string;
  readonly severity: Severity;
  readonly path: string;
  readonly message: string;
}

export interface ParsedDocument {
  readonly path: string;
  readonly frontmatter: Readonly<Record<string, FrontmatterValue>>;
  readonly frontmatterJson: string;
  readonly body: string;
  readonly conceptType: string;
  readonly title: string | null;
  readonly description: string | null;
}

export interface ConceptRecord {
  readonly conceptId: string;
  readonly logicalKey: string;
  readonly path: string;
  readonly conceptType: string;
  readonly title: string | null;
  readonly description: string | null;
  readonly frontmatterJson: string;
  readonly body: string;
}

export interface ReservedRecord {
  readonly path: string;
  readonly filename: "index.md" | "log.md";
  readonly body: string;
}

export interface LinkRecord {
  readonly sourceId: string;
  readonly rawTarget: string;
  readonly targetId: string | null;
  readonly exists: boolean;
  readonly origin: string;
}

export interface Bundle {
  readonly root: string;
  readonly concepts: readonly ConceptRecord[];
  readonly reserved: readonly ReservedRecord[];
  readonly links: readonly LinkRecord[];
  readonly diagnostics: readonly Diagnostic[];
  readonly markdownCount: number;
}

export interface LoadOptions {
  readonly exclude?: readonly string[];
  readonly signal?: AbortSignal;
}

function compareDiagnostics(left: Diagnostic, right: Diagnostic): number {
  return [left.path, left.severity, left.code, left.message]
    .join("\u0000")
    .localeCompare([right.path, right.severity, right.code, right.message].join("\u0000"), "en");
}

export interface CheckOptions extends LoadOptions {
  readonly requireSpec?: string;
  readonly normativeSpec?: boolean;
}

export interface CheckReport {
  readonly root: string;
  readonly conformant: boolean;
  readonly markdown_count: number;
  readonly concept_count: number;
  readonly reserved_count: number;
  readonly diagnostics: readonly Diagnostic[];
}

export interface InventoryReport {
  readonly root: string;
  readonly types: readonly {
    readonly concept_type: string;
    readonly concept_count: number;
  }[];
}

export interface GraphReport {
  readonly root: string;
  readonly nodes: number;
  readonly edges: number;
  readonly weakly_connected_components: number;
  readonly strongly_connected_components: number;
  readonly directed_acyclic: boolean;
}

export class OkfParserError extends Error {
  readonly code: string;
  readonly path?: string;
  readonly details?: Readonly<Record<string, JsonValue>>;

  constructor(
    code: string,
    message: string,
    options: {
      readonly path?: string;
      readonly details?: Readonly<Record<string, JsonValue>>;
      readonly cause?: unknown;
    } = {},
  ) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    this.name = new.target.name;
    this.code = code;
    if (options.path !== undefined) this.path = options.path;
    if (options.details !== undefined) this.details = options.details;
  }
}

export class DocumentParseError extends OkfParserError {
  constructor(message: string, options: { readonly path?: string; readonly cause?: unknown } = {}) {
    super("OKF_PARSE", message, options);
  }
}

export class ExclusionFileError extends OkfParserError {
  constructor(filePath: string, cause: unknown) {
    super("OKF_EXCLUSION_FILE", `cannot read exclusion file ${filePath}: ${messageOf(cause)}`, {
      path: filePath,
      cause,
    });
  }
}

const FRONTMATTER_RE = /^(?:\uFEFF)?---[ \t]*\r?\n([\s\S]*?)(?:\r?\n)?---[ \t]*(?:\r?\n([\s\S]*))?$/;
const RESERVED_FILENAMES = new Set(["index.md", "log.md"]);
const IGNORED_DIRECTORIES = new Set([
  ".git",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
  ".ty_cache",
  ".venv",
  "node_modules",
]);
const markdown = new MarkdownIt("commonmark");

const nullTag = {
  tag: "tag:yaml.org,2002:null",
  default: true,
  test: /^(?:~|[Nn]ull|NULL)?$/,
  resolve: (): null => null,
  stringify: (): string => "null",
};

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function errorCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("code" in error)) return null;
  return typeof error.code === "string" ? error.code : null;
}

async function readUtf8(filePath: string): Promise<string> {
  const bytes = await readFile(filePath);
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    throw new DocumentParseError("document must be valid UTF-8", { path: filePath, cause: error });
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value) as object | null;
  return prototype === Object.prototype || prototype === null;
}

function validateFrontmatterValue(value: unknown, ancestors: WeakSet<object>): FrontmatterValue {
  if (value === null) return null;
  if (typeof value === "string") return value;
  if (typeof value !== "object") {
    throw new DocumentParseError("invalid YAML frontmatter: frontmatter contains an unsupported YAML value");
  }
  if (ancestors.has(value)) {
    throw new DocumentParseError("invalid YAML frontmatter: frontmatter contains a cyclic YAML anchor");
  }
  ancestors.add(value);
  try {
    if (Array.isArray(value)) {
      return Object.freeze(value.map((item) => validateFrontmatterValue(item, ancestors)));
    }
    if (!isRecord(value)) {
      throw new DocumentParseError(
        "invalid YAML frontmatter: frontmatter contains an unsupported YAML value",
      );
    }
    const output: Record<string, FrontmatterValue> = {};
    for (const [key, child] of Object.entries(value)) {
      output[key] = validateFrontmatterValue(child, ancestors);
    }
    return Object.freeze(output);
  } finally {
    ancestors.delete(value);
  }
}

function parseFrontmatterBlock(block: string): Readonly<Record<string, FrontmatterValue>> {
  const document = parseYamlDocument(block, {
    schema: "failsafe",
    merge: true,
    uniqueKeys: true,
    customTags: [nullTag],
  });
  if (document.errors.length > 0) {
    throw new DocumentParseError(`invalid YAML frontmatter: ${document.errors[0]?.message ?? "unknown error"}`);
  }
  const loaded: unknown = document.toJS({ maxAliasCount: 100, mapAsMap: false });
  if (loaded === null) return Object.freeze({});
  if (!isRecord(loaded)) {
    throw new DocumentParseError("frontmatter must be a YAML mapping");
  }
  return validateFrontmatterValue(loaded, new WeakSet()) as Readonly<Record<string, FrontmatterValue>>;
}

function stableFrontmatter(value: FrontmatterValue): FrontmatterValue {
  if (Array.isArray(value)) return value.map(stableFrontmatter);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right, "en"))
        .map(([key, child]) => [key, stableFrontmatter(child)]),
    );
  }
  return value;
}

function optionalText(frontmatter: Readonly<Record<string, FrontmatterValue>>, key: string): string | null {
  const value = frontmatter[key];
  return typeof value === "string" ? value : null;
}

export function parseDocumentContent(content: string, documentPath = "<memory>"): ParsedDocument {
  const match = FRONTMATTER_RE.exec(content);
  if (match === null) {
    throw new DocumentParseError("concept must start with YAML frontmatter delimited by ---", {
      path: documentPath,
    });
  }
  let frontmatter: Readonly<Record<string, FrontmatterValue>>;
  try {
    frontmatter = parseFrontmatterBlock(match[1] ?? "");
  } catch (error) {
    if (error instanceof DocumentParseError) {
      throw new DocumentParseError(error.message, { path: documentPath, cause: error });
    }
    throw error;
  }
  const rawType = frontmatter.type;
  const conceptType = typeof rawType === "string" ? rawType.trim() : "";
  return Object.freeze({
    path: documentPath,
    frontmatter,
    frontmatterJson: JSON.stringify(stableFrontmatter(frontmatter)),
    body: match[2] ?? "",
    conceptType,
    title: optionalText(frontmatter, "title"),
    description: optionalText(frontmatter, "description"),
  });
}

export async function parseDocument(input: string | URL): Promise<ParsedDocument> {
  const filePath = input instanceof URL ? fileURLToPath(input) : input;
  let content: string;
  try {
    content = await readUtf8(filePath);
  } catch (error) {
    throw new DocumentParseError(messageOf(error), { path: filePath, cause: error });
  }
  return parseDocumentContent(content, filePath);
}

export function splitOptionalFrontmatter(
  content: string,
): readonly [Readonly<Record<string, FrontmatterValue>> | null, string] {
  const normalized = content.startsWith("\uFEFF") ? content.slice(1) : content;
  if (!normalized.startsWith("---")) return [null, normalized];
  const match = FRONTMATTER_RE.exec(normalized);
  if (match === null) throw new DocumentParseError("invalid YAML frontmatter delimiters");
  return [parseFrontmatterBlock(match[1] ?? ""), match[2] ?? ""];
}

export function isReservedDocument(filePath: string): boolean {
  return RESERVED_FILENAMES.has(path.basename(filePath));
}

export function isMarkdownFilename(name: string): boolean {
  return name.toLowerCase().endsWith(".md");
}

export function conceptId(bundleRoot: string, filePath: string): string {
  const relative = toPosix(path.relative(bundleRoot, filePath));
  return relative.slice(0, relative.length - path.extname(relative).length);
}

export function iterMarkdownLinks(body: string): readonly string[] {
  const links: string[] = [];
  const pending = [...markdown.parse(body, {})].reverse();
  while (pending.length > 0) {
    const token = pending.pop();
    if (token === undefined) continue;
    if (token.children !== null) pending.push(...[...token.children].reverse());
    if (token.type !== "link_open") continue;
    const destination = token.attrGet("href");
    if (destination !== null) links.push(destination);
  }
  return links;
}

export function iterHeadings(body: string): readonly (readonly [number, string])[] {
  const tokens = markdown.parse(body, {});
  const headings: Array<readonly [number, string]> = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token?.type !== "heading_open") continue;
    const inline = tokens[index + 1];
    const level = Number(token.tag.slice(1));
    headings.push([level, inline?.type === "inline" ? inline.content : ""]);
  }
  return headings;
}

function localTargetPath(rawTarget: string): string | null {
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/u.test(rawTarget) || rawTarget.startsWith("//")) return null;
  const undecoded = rawTarget.split(/[?#]/u, 1)[0] ?? "";
  if (undecoded === "") return null;
  try {
    return decodeURIComponent(undecoded);
  } catch {
    return null;
  }
}

export function hasMarkdownSuffix(rawTarget: string): boolean {
  const targetPath = localTargetPath(rawTarget);
  return targetPath !== null && isMarkdownFilename(targetPath);
}

export function looksLikeFrontmatterLink(value: string): boolean {
  return !/\s/u.test(value) && hasMarkdownSuffix(value);
}

export function resolveLocalTarget(
  bundleRoot: string,
  sourcePath: string,
  rawTarget: string,
): string | null {
  const targetPath = localTargetPath(rawTarget);
  if (targetPath === null || targetPath === "/") return null;
  const candidate = targetPath.startsWith("/")
    ? path.resolve(bundleRoot, `.${targetPath}`)
    : path.resolve(path.dirname(sourcePath), targetPath);
  const relative = path.relative(path.resolve(bundleRoot), candidate);
  if (relative === "" || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) return null;
  return candidate;
}

function toPosix(value: string): string {
  return value.split(path.sep).join("/");
}

function normalizePattern(pattern: string): string {
  return pattern.replace(/^\/+|\/+$/gu, "");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

function segmentExpression(segment: string): string {
  let expression = "";
  for (const character of segment) {
    if (character === "*") expression += "[^/]*";
    else if (character === "?") expression += "[^/]";
    else expression += escapeRegExp(character);
  }
  return expression;
}

function compilePattern(pattern: string): RegExp {
  const segments = pattern.split("/");
  let expression = "";
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index] ?? "";
    if (segment === "**") {
      expression += index < segments.length - 1 ? "(?:[^/]+/)*" : ".*";
      continue;
    }
    expression += segmentExpression(segment);
    if (index < segments.length - 1) expression += "/";
  }
  return new RegExp(`^(?:${expression})$`, "u");
}

function ancestors(relative: string): readonly string[] {
  const segments = relative.split("/");
  return segments.map((_, index) => segments.slice(0, segments.length - index).join("/"));
}

export class ExclusionRules {
  readonly patterns: readonly string[];
  readonly #expressions: readonly RegExp[];

  constructor(patterns: readonly string[] = []) {
    this.patterns = Object.freeze([...patterns]);
    this.#expressions = Object.freeze(
      patterns.map(normalizePattern).filter(Boolean).map(compilePattern),
    );
  }

  static async read(root: string, extra: readonly string[] = []): Promise<ExclusionRules> {
    const exclusionPath = path.join(root, ".okfignore");
    const patterns: string[] = [];
    try {
      const text = await readUtf8(exclusionPath);
      for (const line of text.split(/\r?\n/u)) {
        const stripped = line.trim();
        if (stripped !== "" && !stripped.startsWith("#")) patterns.push(stripped);
      }
    } catch (error) {
      if (errorCode(error) !== "ENOENT") throw new ExclusionFileError(exclusionPath, error);
    }
    patterns.push(...extra);
    return new ExclusionRules(patterns);
  }

  excludes(relative: string): boolean {
    return ancestors(relative).some((ancestor) =>
      this.#expressions.some((expression) => expression.test(ancestor)),
    );
  }
}

export async function discoverMarkdown(
  rootInput: string | URL,
  options: LoadOptions = {},
): Promise<readonly string[]> {
  const root = path.resolve(rootInput instanceof URL ? fileURLToPath(rootInput) : rootInput);
  const rules = await ExclusionRules.read(root, options.exclude ?? []);
  const output: string[] = [];

  async function walk(directory: string): Promise<void> {
    options.signal?.throwIfAborted();
    const entries = await readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
    for (const entry of entries) {
      options.signal?.throwIfAborted();
      const candidate = path.join(directory, entry.name);
      const relative = toPosix(path.relative(root, candidate));
      if (rules.excludes(relative)) continue;
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        if (!IGNORED_DIRECTORIES.has(entry.name)) await walk(candidate);
      } else if (entry.isFile() && isMarkdownFilename(entry.name)) {
        output.push(candidate);
      }
    }
  }

  const stat = await lstat(root);
  if (!stat.isDirectory()) throw new OkfParserError("OKF_NOT_DIRECTORY", `bundle root is not a directory: ${root}`);
  await walk(root);
  return Object.freeze(output.sort((left, right) => left.localeCompare(right, "en")));
}

function frontmatterLinks(
  value: FrontmatterValue,
  fieldPath = "frontmatter",
): readonly (readonly [string, string])[] {
  if (Array.isArray(value)) {
    return value.flatMap((child, index) => frontmatterLinks(child, `${fieldPath}[${index}]`));
  }
  if (value !== null && typeof value === "object") {
    return Object.entries(value).flatMap(([key, child]) =>
      frontmatterLinks(child, `${fieldPath}.${key}`),
    );
  }
  return typeof value === "string" && looksLikeFrontmatterLink(value)
    ? [[value, fieldPath] as const]
    : [];
}

function diagnostic(
  code: string,
  severity: Severity,
  diagnosticPath: string,
  message: string,
): Diagnostic {
  return Object.freeze({ code, severity, path: diagnosticPath, message });
}

function hasTitle(body: string): boolean {
  return iterHeadings(body).some(([level, text]) => level === 1 && text.trim() !== "");
}

function isRealIsoDate(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/u.exec(value);
  if (match === null) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1 || month < 1 || month > 12 || day < 1) return false;
  return day <= new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function validateIndex(root: string, filePath: string, text: string): readonly [string, Diagnostic[]] {
  const relative = toPosix(path.relative(root, filePath));
  const diagnostics: Diagnostic[] = [];
  let frontmatter: Readonly<Record<string, FrontmatterValue>> | null;
  let body: string;
  try {
    [frontmatter, body] = splitOptionalFrontmatter(text);
  } catch (error) {
    return [text, [diagnostic("OKF004", "error", relative, messageOf(error))]];
  }
  if (frontmatter !== null) {
    if (path.dirname(filePath) !== root) {
      diagnostics.push(
        diagnostic("OKF004", "error", relative, "only the bundle-root index.md may contain frontmatter"),
      );
    } else if (Object.keys(frontmatter).some((key) => key !== "okf_version")) {
      diagnostics.push(
        diagnostic("OKF004", "error", relative, "root index.md frontmatter may contain only okf_version"),
      );
    }
  }
  if (!hasTitle(body)) {
    diagnostics.push(
      diagnostic("OKF005", "error", relative, "index.md must contain at least one level-one section"),
    );
  }
  return [body, diagnostics];
}

function validateLog(root: string, filePath: string, text: string): readonly [string, Diagnostic[]] {
  const relative = toPosix(path.relative(root, filePath));
  const diagnostics: Diagnostic[] = [];
  let frontmatter: Readonly<Record<string, FrontmatterValue>> | null;
  let body: string;
  try {
    [frontmatter, body] = splitOptionalFrontmatter(text);
  } catch (error) {
    return [text, [diagnostic("OKF006", "error", relative, messageOf(error))]];
  }
  if (frontmatter !== null) {
    diagnostics.push(diagnostic("OKF006", "error", relative, "log.md must not contain frontmatter"));
  }
  if (!hasTitle(body)) {
    diagnostics.push(diagnostic("OKF007", "error", relative, "log.md must contain a level-one title"));
  }
  const parsedDates: string[] = [];
  for (const [level, heading] of iterHeadings(body)) {
    if (level !== 2) continue;
    if (!/^\d{4}-\d{2}-\d{2}$/u.test(heading)) {
      diagnostics.push(
        diagnostic("OKF008", "error", relative, `log date heading must use YYYY-MM-DD: ${heading}`),
      );
    } else if (!isRealIsoDate(heading)) {
      diagnostics.push(
        diagnostic("OKF008", "error", relative, `log date heading is not a real date: ${heading}`),
      );
    } else {
      parsedDates.push(heading);
    }
  }
  const sortedDates = [...parsedDates].sort().reverse();
  if (parsedDates.some((value, index) => value !== sortedDates[index])) {
    diagnostics.push(
      diagnostic("OKF009", "error", relative, "log date groups must be ordered newest first"),
    );
  }
  return [body, diagnostics];
}

export async function loadBundle(
  rootInput: string | URL,
  options: LoadOptions = {},
): Promise<Bundle> {
  const root = path.resolve(rootInput instanceof URL ? fileURLToPath(rootInput) : rootInput);
  const paths = await discoverMarkdown(root, options);
  const knownPaths = new Set(paths.map((item) => path.resolve(item)));
  const concepts: ConceptRecord[] = [];
  const reserved: ReservedRecord[] = [];
  const links: LinkRecord[] = [];
  const diagnostics: Diagnostic[] = [];

  for (const filePath of paths) {
    options.signal?.throwIfAborted();
    const relative = toPosix(path.relative(root, filePath));
    if (isReservedDocument(filePath)) {
      let text: string;
      try {
        text = await readUtf8(filePath);
      } catch (error) {
        diagnostics.push(diagnostic("OKF003", "error", relative, messageOf(error)));
        continue;
      }
      const [body, reservedDiagnostics] =
        path.basename(filePath) === "index.md"
          ? validateIndex(root, filePath, text)
          : validateLog(root, filePath, text);
      diagnostics.push(...reservedDiagnostics);
      reserved.push(
        Object.freeze({
          path: relative,
          filename: path.basename(filePath) as "index.md" | "log.md",
          body,
        }),
      );
      continue;
    }

    let parsed: ParsedDocument;
    try {
      parsed = await parseDocument(filePath);
    } catch (error) {
      diagnostics.push(diagnostic("OKF001", "error", relative, messageOf(error)));
      continue;
    }
    if (parsed.conceptType === "") {
      diagnostics.push(
        diagnostic("OKF002", "error", relative, "frontmatter must contain a non-empty string type"),
      );
    }
    const sourceId = conceptId(root, filePath);
    const rawLinks: Array<readonly [string, string]> = iterMarkdownLinks(parsed.body).map((target) => [
      target,
      "body",
    ]);
    rawLinks.push(...frontmatterLinks(parsed.frontmatter));
    for (const [rawTarget, origin] of rawLinks) {
      const resolved = resolveLocalTarget(root, filePath, rawTarget);
      if (resolved === null || !hasMarkdownSuffix(rawTarget)) continue;
      const exists = knownPaths.has(resolved);
      const targetId = exists && !isReservedDocument(resolved) ? conceptId(root, resolved) : null;
      links.push(Object.freeze({ sourceId, rawTarget, targetId, exists, origin }));
      if (!exists) {
        diagnostics.push(
          diagnostic("OKF101", "warning", relative, `local Markdown link does not resolve: ${rawTarget}`),
        );
      }
    }
    concepts.push(
      Object.freeze({
        conceptId: sourceId,
        logicalKey: sourceId,
        path: relative,
        conceptType: parsed.conceptType,
        title: parsed.title,
        description: parsed.description,
        frontmatterJson: parsed.frontmatterJson,
        body: parsed.body,
      }),
    );
  }

  diagnostics.sort(compareDiagnostics);
  return Object.freeze({
    root,
    concepts: Object.freeze(concepts),
    reserved: Object.freeze(reserved),
    links: Object.freeze(links),
    diagnostics: Object.freeze(diagnostics),
    markdownCount: paths.length,
  });
}

export async function checkBundle(
  root: string | URL,
  options: CheckOptions = {},
): Promise<CheckReport> {
  const bundle = await loadBundle(root, options);
  const diagnostics = [...bundle.diagnostics];
  if (options.requireSpec !== undefined) {
    diagnostics.push(
      ...(await missingTypeSpecs(
        bundle.root,
        bundle.concepts.map((concept) => concept.conceptType),
        options.requireSpec,
        { normative: options.normativeSpec === true },
      )),
    );
    diagnostics.sort(compareDiagnostics);
  }
  return Object.freeze({
    root: bundle.root,
    conformant: !diagnostics.some((item) => item.severity === "error"),
    markdown_count: bundle.markdownCount,
    concept_count: bundle.concepts.length,
    reserved_count: bundle.reserved.length,
    diagnostics: Object.freeze(diagnostics),
  });
}

export async function inventoryBundle(
  root: string | URL,
  options: LoadOptions = {},
): Promise<InventoryReport> {
  const bundle = await loadBundle(root, options);
  const counts = new Map<string, number>();
  for (const concept of bundle.concepts) {
    counts.set(concept.conceptType, (counts.get(concept.conceptType) ?? 0) + 1);
  }
  return Object.freeze({
    root: bundle.root,
    types: Object.freeze(
      [...counts]
        .sort(([left], [right]) => left.localeCompare(right, "en"))
        .map(([concept_type, concept_count]) => Object.freeze({ concept_type, concept_count })),
    ),
  });
}

function graphNodes(bundle: Bundle): readonly string[] {
  return bundle.concepts.map((item) => item.conceptId);
}

function graphEdges(bundle: Bundle): readonly (readonly [string, string])[] {
  const nodes = new Set(graphNodes(bundle));
  return bundle.links.flatMap((link) =>
    link.targetId !== null && nodes.has(link.targetId) ? [[link.sourceId, link.targetId] as const] : [],
  );
}

function adjacency(nodes: readonly string[], edges: readonly (readonly [string, string])[]): Map<string, string[]> {
  const result = new Map(nodes.map((node) => [node, [] as string[]]));
  for (const [source, target] of edges) result.get(source)?.push(target);
  for (const targets of result.values()) targets.sort((left, right) => left.localeCompare(right, "en"));
  return result;
}

function weakComponentCount(nodes: readonly string[], edges: readonly (readonly [string, string])[]): number {
  const neighbors = new Map(nodes.map((node) => [node, new Set<string>()]));
  for (const [source, target] of edges) {
    neighbors.get(source)?.add(target);
    neighbors.get(target)?.add(source);
  }
  const visited = new Set<string>();
  let count = 0;
  for (const node of nodes) {
    if (visited.has(node)) continue;
    count += 1;
    const stack = [node];
    while (stack.length > 0) {
      const current = stack.pop();
      if (current === undefined || visited.has(current)) continue;
      visited.add(current);
      stack.push(...(neighbors.get(current) ?? []));
    }
  }
  return count;
}

function strongComponentCount(nodes: readonly string[], edges: readonly (readonly [string, string])[]): number {
  const graph = adjacency(nodes, edges);
  let index = 0;
  let components = 0;
  const stack: string[] = [];
  const onStack = new Set<string>();
  const indexes = new Map<string, number>();
  const lowLinks = new Map<string, number>();

  function visit(node: string): void {
    indexes.set(node, index);
    lowLinks.set(node, index);
    index += 1;
    stack.push(node);
    onStack.add(node);
    for (const target of graph.get(node) ?? []) {
      if (!indexes.has(target)) {
        visit(target);
        lowLinks.set(node, Math.min(lowLinks.get(node) ?? 0, lowLinks.get(target) ?? 0));
      } else if (onStack.has(target)) {
        lowLinks.set(node, Math.min(lowLinks.get(node) ?? 0, indexes.get(target) ?? 0));
      }
    }
    if (lowLinks.get(node) !== indexes.get(node)) return;
    while (stack.length > 0) {
      const member = stack.pop();
      if (member === undefined) break;
      onStack.delete(member);
      if (member === node) break;
    }
    components += 1;
  }

  for (const node of nodes) if (!indexes.has(node)) visit(node);
  return components;
}

function isDirectedAcyclic(nodes: readonly string[], edges: readonly (readonly [string, string])[]): boolean {
  const graph = adjacency(nodes, edges);
  const state = new Map<string, 0 | 1 | 2>();
  function visit(node: string): boolean {
    const current = state.get(node) ?? 0;
    if (current === 1) return false;
    if (current === 2) return true;
    state.set(node, 1);
    for (const target of graph.get(node) ?? []) if (!visit(target)) return false;
    state.set(node, 2);
    return true;
  }
  return nodes.every(visit);
}

export async function graphBundle(
  root: string | URL,
  options: LoadOptions = {},
): Promise<GraphReport> {
  const bundle = await loadBundle(root, options);
  const nodes = graphNodes(bundle);
  const edges = graphEdges(bundle);
  return Object.freeze({
    root: bundle.root,
    nodes: nodes.length,
    edges: edges.length,
    weakly_connected_components: weakComponentCount(nodes, edges),
    strongly_connected_components: strongComponentCount(nodes, edges),
    directed_acyclic: isDirectedAcyclic(nodes, edges),
  });
}
