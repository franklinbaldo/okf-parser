import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";

import {
  iterHeadings,
  iterMarkdownLinks,
  discoverMarkdown,
  loadBundle,
  parseDocumentContent,
} from "../typescript/dist/index.js";

function documentSource(index, bodyParagraphs) {
  const body = Array.from(
    { length: bodyParagraphs },
    (_, part) => `Paragraph ${part} with [next](concept-${index + 1}.md).`,
  ).join("\n\n");
  return [
    "---",
    "type: Benchmark",
    `title: Concept ${index}`,
    `ordinal: ${String(index).padStart(6, "0")}`,
    "---",
    `# Concept ${index}`,
    "",
    body,
    "",
  ].join("\n");
}

function splitFrontmatterSource(content) {
  const normalized = content.startsWith("\uFEFF") ? content.slice(1) : content;
  const openingEnd = normalized.indexOf("\n");
  if (openingEnd < 0) return null;
  const delimiter = (line) => {
    const withoutLf = line.endsWith("\n") ? line.slice(0, -1) : line;
    const value = withoutLf.endsWith("\r") ? withoutLf.slice(0, -1) : withoutLf;
    return value.startsWith("---") && /^[ \t]*$/u.test(value.slice(3));
  };
  if (!delimiter(normalized.slice(0, openingEnd + 1))) return null;

  let cursor = openingEnd + 1;
  while (cursor <= normalized.length) {
    const newline = normalized.indexOf("\n", cursor);
    const lineEnd = newline < 0 ? normalized.length : newline + 1;
    if (delimiter(normalized.slice(cursor, lineEnd))) {
      let blockEnd = cursor;
      if (blockEnd > openingEnd + 1 && normalized[blockEnd - 1] === "\n") {
        blockEnd -= 1;
        if (blockEnd > openingEnd + 1 && normalized[blockEnd - 1] === "\r") blockEnd -= 1;
      }
      return [normalized.slice(openingEnd + 1, blockEnd), normalized.slice(lineEnd)];
    }
    if (newline < 0) break;
    cursor = lineEnd;
  }
  return null;
}

function medianNs(operation, rounds) {
  operation();
  const samples = [];
  for (let round = 0; round < rounds; round += 1) {
    const started = performance.now();
    operation();
    samples.push((performance.now() - started) * 1_000_000);
  }
  samples.sort((left, right) => left - right);
  return Math.trunc(samples[Math.floor(samples.length / 2)]);
}

async function medianAsyncNs(operation, rounds) {
  await operation();
  const samples = [];
  for (let round = 0; round < rounds; round += 1) {
    const started = performance.now();
    await operation();
    samples.push((performance.now() - started) * 1_000_000);
  }
  samples.sort((left, right) => left - right);
  return Math.trunc(samples[Math.floor(samples.length / 2)]);
}

async function readAllBounded(paths, concurrency) {
  let next = 0;
  async function worker() {
    while (next < paths.length) {
      const index = next;
      next += 1;
      const filePath = paths[index];
      if (filePath !== undefined) await readFile(filePath);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(concurrency, paths.length) }, () => worker()),
  );
}

async function measure(size, bodyParagraphs, rounds) {
  const documents = Array.from({ length: size }, (_, index) => documentSource(index, bodyParagraphs));
  const bodies = documents.map((source) => splitFrontmatterSource(source)[1]);

  const splitNs = medianNs(() => documents.map(splitFrontmatterSource), rounds);
  const parseNs = medianNs(
    () => documents.map((source, index) => parseDocumentContent(source, `concept-${index}.md`)),
    rounds,
  );
  const linksNs = medianNs(() => bodies.map(iterMarkdownLinks), rounds);
  const headingsNs = medianNs(() => bodies.map(iterHeadings), rounds);

  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-benchmark-"));
  try {
    await writeFile(path.join(root, "index.md"), "# Benchmark bundle\n", "utf8");
    await Promise.all(
      documents.map((source, index) =>
        writeFile(path.join(root, `concept-${index}.md`), source, "utf8"),
      ),
    );
    const paths = await discoverMarkdown(root);
    const discoveryNs = await medianAsyncNs(() => discoverMarkdown(root), rounds);
    const sequentialReadNs = await medianAsyncNs(async () => {
      for (const filePath of paths) await readFile(filePath);
    }, rounds);
    const concurrentReadNs = {};
    for (const concurrency of readConcurrencies) {
      const elapsed = await medianAsyncNs(
        () => readAllBounded(paths, concurrency),
        rounds,
      );
      concurrentReadNs[String(concurrency)] = Math.trunc(elapsed / paths.length);
    }
    const loadNs = await medianAsyncNs(() => loadBundle(root), rounds);
    return {
      documents: size,
      markdown_files: paths.length,
      source_bytes: documents.reduce((total, source) => total + Buffer.byteLength(source), 0),
      frontmatter_split_ns_per_document: Math.trunc(splitNs / size),
      document_parse_ns_per_document: Math.trunc(parseNs / size),
      markdown_links_ns_per_document: Math.trunc(linksNs / size),
      markdown_headings_ns_per_document: Math.trunc(headingsNs / size),
      filesystem_discovery_ns_per_file: Math.trunc(discoveryNs / paths.length),
      filesystem_sequential_read_ns_per_file: Math.trunc(sequentialReadNs / paths.length),
      filesystem_read_ns_per_file_by_concurrency: concurrentReadNs,
      bundle_load_ns_per_document: Math.trunc(loadNs / size),
      bundle_load_ms: Math.trunc(loadNs / 1_000_000),
    };
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

const values = new Map(
  process.argv.slice(2).map((argument) => {
    const [key, value = ""] = argument.replace(/^--/u, "").split("=", 2);
    return [key, value];
  }),
);
const sizes = (values.get("sizes") ?? "100,1000,5000").split(",").filter(Boolean).map(Number);
const bodyParagraphs = Number(values.get("body-paragraphs") ?? 4);
const rounds = Number(values.get("rounds") ?? 5);
const readConcurrencies = (values.get("read-concurrencies") ?? "32")
  .split(",")
  .filter(Boolean)
  .map(Number);
if (
  readConcurrencies.length === 0 ||
  readConcurrencies.some((value) => !Number.isSafeInteger(value) || value < 1)
) {
  throw new Error("--read-concurrencies must contain positive integers");
}
const results = [];
for (const size of sizes) results.push(await measure(size, bodyParagraphs, rounds));

console.log(
  JSON.stringify(
    {
      runtime: "typescript",
      runtime_version: process.version,
      rounds,
      body_paragraphs: bodyParagraphs,
      read_concurrencies: readConcurrencies,
      results,
    },
    null,
    2,
  ),
);
