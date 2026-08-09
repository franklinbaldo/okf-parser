import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import { ingestDocuments } from "../src/index.js";

test("identity projection emits ordered envelopes without parsing content", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-ingestion-identity-"));
  await writeFile(path.join(root, "b.md"), "not frontmatter", "utf8");
  await writeFile(path.join(root, "a.md"), "also not frontmatter", "utf8");

  const envelopes = [];
  for await (const envelope of ingestDocuments(root)) envelopes.push(envelope);

  expect(envelopes.map((item) => item.path)).toEqual(["a.md", "b.md"]);
  expect(envelopes.map((item) => item.ordinal)).toEqual([0, 1]);
  expect(envelopes.every((item) => item.body === undefined)).toBe(true);
});

test("projects frontmatter and Markdown facts without retaining body", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-ingestion-facts-"));
  await writeFile(
    path.join(root, "one.md"),
    "---\ntype: Note\ntitle: One\n---\n# Heading\n\n[Two](two.md)\n",
    "utf8",
  );

  const envelopes = [];
  for await (const envelope of ingestDocuments(root, {
    capabilities: ["frontmatter", "markdown_facts"],
    readConcurrency: 1,
  })) envelopes.push(envelope);

  expect(envelopes[0]).toMatchObject({
    classification: "typed:Note",
    frontmatter: { type: "Note", title: "One" },
    facts: { links: ["two.md"], headings: [[1, "Heading"]] },
  });
  expect(envelopes[0]?.body).toBeUndefined();
});


test("digest capability composes with projection pushdown", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-ingest-digest-"));
  await writeFile(path.join(root, "note.md"), "\uFEFF---\r\ntype: Note\r\n---\r\nBody\r\n");
  const rows = [];
  for await (const row of ingestDocuments(root, { capabilities: ["digests"] })) rows.push(row);
  expect(rows).toHaveLength(1);
  expect(rows[0]?.sourceDigest).toMatch(/^sha256:[0-9a-f]{64}$/u);
  expect(rows[0]?.parsedDigest).toMatch(/^okf-parsed-v1-jcs-sha256:[0-9a-f]{64}$/u);
  expect(rows[0]?.body).toBeUndefined();
  expect(rows[0]?.frontmatter).toBeUndefined();
});
