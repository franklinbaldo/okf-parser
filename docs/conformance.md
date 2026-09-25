---
type: Documentation
title: Upstream OKF conformance corpus
description: How okf-parser proves its OKF v0.2 support against a pinned upstream specification revision
---

# Upstream OKF conformance corpus

The other files in `conformance/` make the Python, Rust and TypeScript engines
agree with each other. `conformance/upstream/` makes them agree with the
[OKF specification](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md).
Without it, three engines could agree and still all disagree with upstream.

## Layout

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

## The clause registry

`UPSTREAM.json` lists each consumer-facing MUST and MUST NOT clause with its
section and a verbatim quote. A clause's `status` is either `covered`, meaning
at least one case cites it, or `not-applicable` with a reason, such as a
producer obligation that no reader can check. A test fails when a covered
clause has no case.

## A case

`case.json` has these keys:

- `clauses` — registry IDs the case exercises;
- `category` — `normative` when the expectation follows from the specification
  text, `policy` when it is okf-parser's reading of soft or structural wording;
- `claim` — one sentence stating what the case proves;
- `expected` — any subset of `conformant`, `concepts`, `reserved`,
  `diagnostics`, `links` and `frontmatter`. Only the keys present are asserted;
- `divergence` — optional. A known disagreement, with a `kind` and a `detail`.

## Reading a failure

- `engine-divergence` — the engines disagree with each other. The Python test
  compares the Python and Rust engines when `OKF_CORE` names a Rust binary.
- `normative-regression` — the engines agree, but no longer do what the
  specification says.
- `policy-regression` — the engines agree, but changed a documented policy.
- A case with `divergence` is a strict expected failure. When the divergence is
  fixed, the test fails until the case drops its `divergence` key.

## Known divergences

- `v0.2/log-with-frontmatter` — every engine reports OKF006 for frontmatter in
  `log.md`. §8 forbids frontmatter only in `index.md`, §9 is silent, and
  upstream's `acme_retail` example ships a `log.md` with frontmatter.
- `v0.2/link-raw-target-is-literal` — the Python and TypeScript engines record
  markdown-it's percent-encoded href as `raw_target`; the Rust engine keeps the
  literal text. Both resolve the link to the same concept.

## Bumping the upstream revision

```sh
git clone https://github.com/GoogleCloudPlatform/knowledge-catalog /tmp/kc
git -C /tmp/kc checkout <commit>
uv run --script scripts/upstream_conformance.py /tmp/kc
```

The script reports a commit or digest mismatch, every registered clause whose
quote is gone from the specification, and every upstream example bundle whose
error codes differ from `examples` in `UPSTREAM.json`. Update the pin, the
clauses and the cases together, so the pull request is the semantic diff.

## Running the corpus

```sh
uv run pytest tests/test_upstream_conformance.py
OKF_CORE=target/release/okf-parser uv run pytest tests/test_upstream_conformance.py
npm test --prefix typescript -- upstream-conformance
```
