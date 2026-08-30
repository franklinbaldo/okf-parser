import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

import {
  GitCommitMessageError,
  formatGitCommitMessage,
  parseGitCommitMessage,
  validateGitCommitMessage,
} from "../src/git-commit.js";

interface ValidCase {
  readonly name: string;
  readonly source: string;
  readonly subject: string;
  readonly authored_metadata: Readonly<Record<string, unknown>>;
  readonly effective_frontmatter: Readonly<Record<string, unknown>>;
  readonly body: string;
  readonly has_envelope: boolean;
  readonly source_digest: string;
  readonly parsed_digest: string;
  readonly formatted: string;
}

interface InvalidCase {
  readonly name: string;
  readonly source: string;
  readonly code: string;
}

async function vectors(): Promise<{
  readonly valid: readonly ValidCase[];
  readonly invalid: readonly InvalidCase[];
}> {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const text = await readFile(path.resolve(here, "../../conformance/git-commit-messages.json"), "utf8");
  return JSON.parse(text) as {
    readonly valid: readonly ValidCase[];
    readonly invalid: readonly InvalidCase[];
  };
}

describe("Git commit message conformance", () => {
  test("matches shared valid vectors", async () => {
    for (const item of (await vectors()).valid) {
      const parsed = parseGitCommitMessage(item.source);
      expect(parsed.subject).toBe(item.subject);
      expect(parsed.authoredMetadata).toEqual(item.authored_metadata);
      expect(parsed.effectiveFrontmatter).toEqual(item.effective_frontmatter);
      expect(parsed.body).toBe(item.body);
      expect(parsed.hasEnvelope).toBe(item.has_envelope);
      expect(parsed.sourceDigest).toBe(item.source_digest);
      expect(parsed.parsedDigest).toBe(item.parsed_digest);

      const formatted = formatGitCommitMessage(parsed);
      expect(formatted).toBe(item.formatted);
      const reparsed = parseGitCommitMessage(formatted);
      expect(formatGitCommitMessage(reparsed)).toBe(formatted);
      expect(reparsed.effectiveFrontmatter).toEqual(parsed.effectiveFrontmatter);
      expect(reparsed.body).toBe(parsed.body);
    }
  });

  test("matches shared invalid vectors", async () => {
    for (const item of (await vectors()).invalid) {
      try {
        parseGitCommitMessage(item.source);
        throw new Error(`expected ${item.name} to fail`);
      } catch (error) {
        expect(error).toBeInstanceOf(GitCommitMessageError);
        expect((error as GitCommitMessageError).code).toBe(item.code);
      }
    }
  });

  test("policy can require envelopes without changing legacy parsing", () => {
    const source = "Legacy commit\n\nStill readable.";
    expect(parseGitCommitMessage(source).hasEnvelope).toBe(false);
    expect(() => validateGitCommitMessage(source, { requireEnvelope: true })).toThrow(
      /requires an OKF commit envelope/u,
    );
  });

  test("effective title and default type participate in parsed digest", () => {
    const first = parseGitCommitMessage("First subject\n\nSame body");
    const second = parseGitCommitMessage("Second subject\n\nSame body");
    const typed = parseGitCommitMessage(
      "First subject\n\n--- okf\ntype: Change\n---\n\nSame body",
    );

    expect(first.parsedDigest).not.toBe(second.parsedDigest);
    expect(first.parsedDigest).not.toBe(typed.parsedDigest);
  });
});
