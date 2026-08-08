import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

import { parseDocumentContent } from "../src/core.js";

interface DigestCase {
  readonly name: string;
  readonly source: string;
  readonly source_digest: string;
  readonly revision_digest: string;
}

async function vectors(): Promise<readonly DigestCase[]> {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const fixture = path.resolve(here, "../../conformance/revision-digests.json");
  const payload = JSON.parse(await readFile(fixture, "utf8")) as { readonly cases: DigestCase[] };
  return payload.cases;
}

test("revision digest vectors match the shared cross-language contract", async () => {
  for (const item of await vectors()) {
    const parsed = parseDocumentContent(item.source, `${item.name}.md`);
    expect(parsed.sourceDigest).toBe(item.source_digest);
    expect(parsed.revisionDigest).toBe(item.revision_digest);
  }
});

test("physical source differences can preserve parsed revision identity", async () => {
  const [canonical, physical, semantic] = await vectors();
  expect(canonical).toBeDefined();
  expect(physical).toBeDefined();
  expect(semantic).toBeDefined();
  if (canonical === undefined || physical === undefined || semantic === undefined) return;

  expect(canonical.source_digest).not.toBe(physical.source_digest);
  expect(canonical.revision_digest).toBe(physical.revision_digest);
  expect(canonical.revision_digest).not.toBe(semantic.revision_digest);
});
