import { performance } from "node:perf_hooks";
import { markdownFacts } from "../typescript/dist/core.js";
import { rustMarkdownFactsBatch } from "../typescript/dist/rust-core.js";

const documents = Number(process.argv[2] ?? 10000);
const rounds = Number(process.argv[3] ?? 5);
const executable = process.argv[4];
if (executable === undefined) throw new Error("usage: rust-facts.mjs DOCUMENTS ROUNDS RUST_CORE");
const bodies = Array.from({ length: documents }, (_, index) =>
  `# Concept ${index}\n\n${Array.from(
    { length: 4 },
    (_, part) => `Paragraph ${part} with [next](concept-${(index + 1) % documents}.md).`,
  ).join("\n\n")}\n`);

const native = bodies.map(markdownFacts);
const rust = await rustMarkdownFactsBatch(bodies, executable);
if (JSON.stringify(native) !== JSON.stringify(rust)) throw new Error("native and Rust facts differ");

async function elapsed(operation) {
  const start = performance.now();
  await operation();
  return performance.now() - start;
}
const nativeRuns = [];
const rustRuns = [];
for (let index = 0; index < rounds; index += 1) {
  nativeRuns.push(await elapsed(() => bodies.map(markdownFacts)));
  rustRuns.push(await elapsed(() => rustMarkdownFactsBatch(bodies, executable)));
}
nativeRuns.sort((a, b) => a - b);
rustRuns.sort((a, b) => a - b);
const nativeMs = nativeRuns[Math.floor(rounds / 2)];
const rustMs = rustRuns[Math.floor(rounds / 2)];
console.log(JSON.stringify({
  documents,
  rounds,
  native_ms: Math.round(nativeMs),
  rust_batch_ms: Math.round(rustMs),
  native_ns_per_document: Math.round(nativeMs * 1e6 / documents),
  rust_batch_ns_per_document: Math.round(rustMs * 1e6 / documents),
  speedup: nativeMs / rustMs,
}, null, 2));
