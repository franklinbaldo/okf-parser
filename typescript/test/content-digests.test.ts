import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

import { parseDocumentContent } from "../src/core.js";

interface DigestCase {
  readonly name: string;
  readonly source: string;
  readonly source_digest: string;
  readonly parsed_digest: string;
}

async function vectors(): Promise<readonly DigestCase[]> {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const text = await readFile(path.resolve(here, "../../conformance/content-digests.json"), "utf8");
  return (JSON.parse(text) as { readonly cases: readonly DigestCase[] }).cases;
}

describe("content digest conformance", () => {
  test("matches shared source and parsed digest vectors", async () => {
    for (const item of await vectors()) {
      const parsed = parseDocumentContent(item.source, `${item.name}.md`);
      expect(parsed.sourceDigest).toBe(item.source_digest);
      expect(parsed.parsedDigest).toBe(item.parsed_digest);
    }
  });

  test("physical BOM/CRLF differences preserve parsed identity", async () => {
    const [canonical, physical] = await vectors();
    expect(canonical?.source_digest).not.toBe(physical?.source_digest);
    expect(canonical?.parsed_digest).toBe(physical?.parsed_digest);
  });
});
