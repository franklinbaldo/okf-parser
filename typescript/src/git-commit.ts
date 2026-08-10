import { parseDocumentContent } from "./core.js";
import type { FrontmatterValue } from "./core.js";
import { canonicalJson, normalizeNewlines, parsedDigest, sourceDigest } from "./digests.js";

const RESERVED_KEYS = new Set([
  "title",
  "source_kind",
  "repository_identity",
  "object_format",
  "object_type",
  "oid",
  "commit_oid",
  "tree_oid",
  "parent_oids",
  "parents",
  "author",
  "committer",
  "refs",
  "source_digest",
  "parsed_digest",
  "diagnostics",
  "provenance",
]);

export class GitCommitMessageError extends Error {
  readonly code: string;
  readonly line?: number;

  constructor(code: string, message: string, line?: number) {
    super(message);
    this.name = "GitCommitMessageError";
    this.code = code;
    if (line !== undefined) this.line = line;
  }
}

export interface GitCommitMessage {
  readonly subject: string;
  readonly authoredMetadata: Readonly<Record<string, FrontmatterValue>>;
  readonly effectiveFrontmatter: Readonly<Record<string, FrontmatterValue>>;
  readonly body: string;
  readonly hasEnvelope: boolean;
  readonly sourceDigest: string;
  readonly parsedDigest: string;
  readonly conceptType: string;
}

function parseMetadata(block: string): Readonly<Record<string, FrontmatterValue>> {
  try {
    return parseDocumentContent(`---\n${block}\n---\n`, "<git-commit-metadata>").frontmatter;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new GitCommitMessageError("GIT_MESSAGE_YAML", message, 3);
  }
}

function validateMetadata(metadata: Readonly<Record<string, FrontmatterValue>>): string {
  const reserved = Object.keys(metadata)
    .filter((key) => RESERVED_KEYS.has(key))
    .sort();
  if (reserved.length > 0) {
    throw new GitCommitMessageError(
      "GIT_MESSAGE_RESERVED_KEY",
      `commit metadata uses adapter-owned key(s): ${reserved.join(", ")}`,
      3,
    );
  }

  if (!("type" in metadata)) return "Commit";
  const rawType = metadata.type;
  if (typeof rawType !== "string" || rawType.trim() === "") {
    throw new GitCommitMessageError(
      "GIT_MESSAGE_TYPE",
      "commit metadata type must be a non-empty string",
      3,
    );
  }
  return rawType.trim();
}

function findClosingDelimiter(envelopeSource: string): readonly [string, string, number] {
  const lines = envelopeSource.match(/.*(?:\n|$)/gu)?.filter((line) => line !== "") ?? [];
  const first = lines[0]?.endsWith("\n") ? lines[0].slice(0, -1) : lines[0];
  if (first !== "--- okf") {
    throw new GitCommitMessageError(
      "GIT_MESSAGE_ENVELOPE",
      "structured commit metadata must start with exact '--- okf'",
      3,
    );
  }

  let offset = lines[0]?.length ?? 0;
  for (let index = 1; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    const logical = line.endsWith("\n") ? line.slice(0, -1) : line;
    if (logical === "---") {
      const metadata = envelopeSource.slice(lines[0]?.length ?? 0, offset).replace(/\n$/u, "");
      const after = envelopeSource.slice(offset + line.length);
      return [metadata, after, index + 3] as const;
    }
    offset += line.length;
  }

  throw new GitCommitMessageError(
    "GIT_MESSAGE_UNTERMINATED_ENVELOPE",
    "structured commit metadata has no closing '---' delimiter",
    3,
  );
}

export function parseGitCommitMessage(source: string): GitCommitMessage {
  const exactDigest = sourceDigest(source);
  const normalized = normalizeNewlines(source).replace(/^\uFEFF/u, "");
  const newline = normalized.indexOf("\n");
  const subject = newline < 0 ? normalized : normalized.slice(0, newline);
  const remainder = newline < 0 ? "" : normalized.slice(newline + 1);
  if (subject.trim() === "") {
    throw new GitCommitMessageError("GIT_MESSAGE_EMPTY_SUBJECT", "commit subject must be non-empty", 1);
  }

  const hasBlankSeparator = newline >= 0 && remainder.startsWith("\n");
  const candidate = hasBlankSeparator ? remainder.slice(1) : remainder;
  const hasEnvelope = candidate === "--- okf" || candidate.startsWith("--- okf\n");

  let metadata: Readonly<Record<string, FrontmatterValue>>;
  let body: string;
  if (hasEnvelope) {
    const [block, after, closingLine] = findClosingDelimiter(candidate);
    if (after !== "" && !after.startsWith("\n")) {
      throw new GitCommitMessageError(
        "GIT_MESSAGE_BODY_SEPARATOR",
        "structured commit metadata must be followed by a blank line before the body",
        closingLine + 1,
      );
    }
    body = after.startsWith("\n") ? after.slice(1) : "";
    if (body.split("\n").some((line) => line === "--- okf")) {
      throw new GitCommitMessageError(
        "GIT_MESSAGE_DUPLICATE_ENVELOPE",
        "commit message contains more than one OKF envelope",
      );
    }
    metadata = parseMetadata(block);
  } else {
    metadata = Object.freeze({});
    body = hasBlankSeparator ? candidate : remainder;
  }

  const effectiveType = validateMetadata(metadata);
  const effectiveFrontmatter = Object.freeze({ ...metadata, type: effectiveType, title: subject });
  return Object.freeze({
    subject,
    authoredMetadata: Object.freeze({ ...metadata }),
    effectiveFrontmatter,
    body,
    hasEnvelope,
    sourceDigest: exactDigest,
    parsedDigest: parsedDigest(effectiveFrontmatter, body),
    conceptType: effectiveType,
  });
}

export function validateGitCommitMessage(
  source: string,
  options: { readonly requireEnvelope?: boolean } = {},
): GitCommitMessage {
  const parsed = parseGitCommitMessage(source);
  if (options.requireEnvelope === true && !parsed.hasEnvelope) {
    throw new GitCommitMessageError(
      "GIT_MESSAGE_ENVELOPE_REQUIRED",
      "repository policy requires an OKF commit envelope",
      3,
    );
  }
  return parsed;
}

function formatMetadata(metadata: Readonly<Record<string, FrontmatterValue>>): string {
  return Object.keys(metadata)
    .sort()
    .map((key) => `${canonicalJson(key)}: ${canonicalJson(metadata[key] ?? null)}`)
    .join("\n");
}

export function formatGitCommitMessage(message: GitCommitMessage): string {
  if (!message.hasEnvelope) {
    return message.body === "" ? message.subject : `${message.subject}\n\n${message.body}`;
  }

  const metadata = formatMetadata(message.authoredMetadata);
  let block = "--- okf\n";
  if (metadata !== "") block += `${metadata}\n`;
  block += "---";
  return message.body === ""
    ? `${message.subject}\n\n${block}`
    : `${message.subject}\n\n${block}\n\n${message.body}`;
}
