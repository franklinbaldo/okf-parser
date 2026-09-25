---
type: Release Note
title: Upstream OKF conformance corpus
---

# Upstream OKF conformance corpus

`conformance/upstream/` pins the OKF v0.2 specification to an upstream commit
and SHA-256 digest, registers each consumer-facing MUST and MUST NOT clause
with its verbatim quote, and holds one fixture bundle per claim. The Python,
Rust and TypeScript engines execute the same `case.json` expectations, and a
failure reports an engine divergence, a normative or policy regression, or a
change to a recorded divergence.

A known divergence records the exact value each affected engine produces for
each divergent field, and every other field stays asserted, so a divergence
never masks an unrelated regression. The corpus records two:

- all engines reject `log.md` frontmatter (OKF006), which §9 does not forbid
  and upstream's own `acme_retail` example uses;
- the Python and TypeScript engines record markdown-it's percent-encoded href
  as a link's `raw_target`, while the Rust engine keeps the literal target.

`scripts/upstream_conformance.py` checks a local upstream checkout against the
pin, so bumping the upstream revision shows which quoted clauses changed.
