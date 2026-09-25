import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";

import { describe, expect, test } from "vitest";

import { loadBundle, type Bundle } from "../src/index.js";

// Mirrors tests/test_upstream_conformance.py: only the keys present in
// `expected` are asserted. A known divergence records the exact value this
// engine produces for each divergent field; those fields are held to the
// recorded value and every other field stays asserted against `expected`.
const corpus = path.resolve(import.meta.dirname, "../../conformance/upstream");

interface UpstreamCase {
  readonly claim: string;
  readonly category: "normative" | "policy";
  readonly expected: Readonly<Record<string, unknown>>;
  readonly divergence?: {
    readonly kind: string;
    readonly detail: string;
    readonly observed: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
  };
}

function observe(bundle: Bundle): Record<string, unknown> {
  return {
    conformant: !bundle.diagnostics.some((item) => item.severity === "error"),
    concepts: Object.fromEntries(bundle.concepts.map((row) => [row.path, row.conceptType])),
    reserved: bundle.reserved.map((row) => row.path).sort(),
    diagnostics: bundle.diagnostics.map(({ code, severity, path: file }) => ({
      code,
      severity,
      path: file,
    })),
    links: bundle.links.map((row) => ({
      source: row.sourceId,
      raw_target: row.rawTarget,
      target: row.targetId,
      exists: row.exists,
    })),
    frontmatter: Object.fromEntries(
      bundle.concepts.map((row) => [row.path, JSON.parse(row.frontmatterJson) as unknown]),
    ),
  };
}

const cases: string[] = [];
for (const version of (await readdir(corpus, { withFileTypes: true })).filter((entry) =>
  entry.isDirectory(),
)) {
  for (const name of await readdir(path.join(corpus, version.name))) {
    cases.push(`${version.name}/${name}`);
  }
}
cases.sort();

describe("upstream OKF conformance corpus", () => {
  test.each(cases)("%s", async (name) => {
    const directory = path.join(corpus, name);
    const item = JSON.parse(
      await readFile(path.join(directory, "case.json"), "utf8"),
    ) as UpstreamCase;
    const observed = observe(await loadBundle(path.join(directory, "bundle")));
    const recorded = item.divergence?.observed.typescript ?? {};
    const wanted: Record<string, unknown> = { ...item.expected, ...recorded };
    const mismatches = Object.keys(wanted).filter(
      (key) => !isDeepStrictEqual(observed[key], wanted[key]),
    );
    const report = (keys: readonly string[]): Record<string, unknown> =>
      Object.fromEntries(keys.map((key) => [key, { expected: wanted[key], observed: observed[key] }]));
    expect(
      report(mismatches.filter((key) => key in recorded)),
      "divergence-changed: the recorded divergence was fixed or shifted; update the case",
    ).toEqual({});
    expect(report(mismatches), `${item.category}-regression: ${item.claim}`).toEqual({});
  });
});
