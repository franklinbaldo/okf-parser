---
type: Documentation
title: Conformance corpus
description: The versioned, cross-language compatibility contract with the OKF spec
---

# Conformance corpus

This directory is the versioned, executable compatibility contract between
`okf-parser` and the [Open Knowledge Format
specification](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md),
and between the Python, TypeScript, and Rust implementations of this parser.
Every fixture here is data, not implementation: each language's test suite
loads the same JSON and asserts the same expectations, so the three
implementations cannot silently drift from each other while still passing
their own hand-written tests.

`MANIFEST.json` is the index. It records:

- where the upstream specification is pinned (`spec.pin`, which names
  `upstream/UPSTREAM.json`, the single record of the upstream commit and
  `okf/SPEC.md` digest this corpus was checked against);
- the list of fixture files, what area of behavior each covers, and which
  language suites consume it (`shared_with`).

`tests/test_conformance_manifest.py` enforces that the manifest and the
directory never drift apart: every `.json` file in `conformance/` must be
listed, and every listed file must exist.

## Adding or changing a fixture

1. Add cases to the relevant `conformance/*.json` file (or add a new file and
   register it in `MANIFEST.json`).
2. If the change tracks a new upstream spec revision, follow
   [Bumping the upstream revision](#bumping-the-upstream-revision) and bump
   `corpus_version`.
3. Add or update the loader test(s) in each language's suite that reads the
   fixture (Python under `tests/`, TypeScript under `typescript/test/`).

## Regressions (`regressions.json`)

`regressions.json` is append-only: once a case lands, it is never edited or
removed, even if the surrounding code is later rewritten. This is what makes
a fixed bug stay fixed — a future change that reintroduces the old behavior
fails a fixture written specifically against it, not a hand-written test that
may have been deleted along with the code it covered.

When you fix a bug that a user could hit (as opposed to an internal
refactor), add a case with:

- `id` — a short, stable, kebab-case identifier;
- `issue` — the GitHub issue or PR number that reported or fixed it;
- `input` — the minimal reproduction;
- `expected` — the correct behavior.

Cases are grouped by the area they exercise so each language suite can load
only the areas it implements.

## Upstream conformance (`upstream/`)

The top-level fixtures make the Python, Rust and TypeScript engines agree with
each other. `upstream/` makes them agree with the specification itself.
Without it, three engines could agree and still all disagree with upstream.

### Layout

```text
conformance/upstream/
  UPSTREAM.json          pinned commit, SHA-256 digest, clause registry, example verdicts
  v0.2/<case>/
    case.json            the claim and what a consumer must observe
    bundle/              a minimal fixture bundle
  v0.1/<case>/           v0.1 documents a v0.2 consumer must still accept
```

Each fixture is derived from the specification rather than copied from it, so
the corpus stays small and every bundle isolates one claim.

### The clause registry

`UPSTREAM.json` lists each consumer-facing MUST and MUST NOT clause with its
section and a verbatim quote. A clause's `status` is either `covered`, meaning
at least one case cites it, or `not-applicable` with a reason, such as a
producer obligation that no reader can check. A test fails when a covered
clause has no case.

### A case

`case.json` has these keys:

- `clauses` — registry IDs the case exercises;
- `category` — `normative` when the expectation follows from the specification
  text, `policy` when it is okf-parser's reading of soft or structural wording;
- `claim` — one sentence stating what the case proves;
- `expected` — any subset of `conformant`, `concepts`, `reserved`,
  `diagnostics`, `links` and `frontmatter`. Only the keys present are asserted;
- `divergence` — optional. A known disagreement, with a `kind`, a `detail`
  and `observed`: for each affected engine (`python`, `rust`, `typescript`),
  the exact value it produces for each divergent field. That engine is held to
  the recorded value for those fields only; every other field in `expected`
  stays asserted, so a divergence never hides an unrelated regression.

### Reading a failure

- `engine-divergence` — the engines disagree on a field no divergence
  declares. The Python test compares the Python and Rust engines when
  `OKF_CORE` names a Rust binary.
- `normative-regression` — a field no longer does what the specification says.
- `policy-regression` — a field changed a documented policy.
- `divergence-changed` — an engine no longer produces the recorded divergent
  value, because the divergence was fixed or shifted. Update or drop the
  `divergence` entry for that engine.

### Known divergences

- `v0.2/log-with-frontmatter` — every engine reports OKF006 for frontmatter in
  `log.md`. §8 forbids frontmatter only in `index.md`, §9 is silent, and
  upstream's `acme_retail` example ships a `log.md` with frontmatter.
- `v0.2/link-raw-target-is-literal` — the Python and TypeScript engines record
  markdown-it's percent-encoded href as `raw_target`; the Rust engine keeps the
  literal text. Both resolve the link to the same concept.

## Bumping the upstream revision

```sh
git clone https://github.com/GoogleCloudPlatform/knowledge-catalog /tmp/kc
uv run --script scripts/upstream_conformance.py /tmp/kc
```

The script compares the pin with the last upstream commit that changed
`okf/SPEC.md`, so a clone of upstream `main` works. It reports a commit or
digest mismatch, every registered clause whose
quote is gone from the specification, and every upstream example bundle whose
error codes differ from `examples` in `UPSTREAM.json`. Update the pin, the
clauses and the cases together, so the pull request is the semantic diff.

## Running the corpus

```sh
uv run pytest tests/test_upstream_conformance.py
OKF_CORE=target/release/okf-parser uv run pytest tests/test_upstream_conformance.py
npm test --prefix typescript -- upstream-conformance
```
