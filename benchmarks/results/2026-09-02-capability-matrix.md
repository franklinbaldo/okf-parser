---
type: BenchmarkResult
title: OKF ecosystem capability matrix
description: Which questions each published OKF tool answers on one shared fixture
---

# Capability matrix — 2026-09-02

Environment: Windows 11 x86_64, Python 3.12, `okf-parser` 0.45.6 from PyPI,
alongside `kbforge-okfquery` 0.1.0, `okflint` 0.4.0, `okf-retrieve` 0.1.1,
`okf-nav` 0.1.0, `okf-schema` 0.12.0 and `google-okf` 0.1.3, all installed from
PyPI into one throwaway environment. Reproduce with:

```bash
uv run --script benchmarks/capability_matrix.py --rival-bin path/to/venv/bin
```

The fixture is nine concepts across four types, holding one link cycle, one
unresolved link, one type with no specification document, and six concepts with
no inbound link. Every expected answer is a property of those documents, so the
benchmark fails when okf-parser is wrong, not only when a rival is.

## Result

| question | okf-parser | okfquery | okflint | okf-retrieve | okf-nav | okf-schema | google-okf |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Is the bundle conformant? | yes | yes | yes | disagrees | — | — | disagrees |
| How many concepts? | yes | scope | files | — | no bundle | — | — |
| How many of each type? | yes | scope | — | — | — | — | — |
| Which have no inbound link? | yes | scope | — | — | — | — | — |
| Are there cycles? | yes | **yes** | — | — | — | — | — |
| How many links do not resolve? | yes | **yes** | — | — | — | — | — |
| Which types have no specification? | yes | scope | — | — | — | — | — |
| What breaks if `a` is deleted? | yes | **yes** | — | — | — | — | — |
| | **8/8** | **4/8** | 1/8 | 0/8 | 0/8 | 0/8 | 0/8 |

A dash means the tool exposes no subcommand that could answer. `scope` means the
tool answered correctly for the documents it looks at, but does not look at the
whole bundle; the cause is the same in all four cases and is explained below.

## The finding that matters

**`kbforge-okfquery` answers the graph questions.** Cycles, impact analysis and
unresolved-link counting are not exclusive to okf-parser: a tool that loads a
bundle into DuckDB and exposes arbitrary SQL reaches all three with recursive
CTEs, and reaches them exactly right. Its impact answer for deleting `a` is
`b, c, d`, identical to okf-parser's.

This benchmark was written expecting the opposite, and the expectation did not
survive. Any claim that only okf-parser can answer questions *about the bundle
as a whole* is false as stated.

What is true is narrower and still worth saying. All four okfquery
disagreements have a single cause: it hard-codes discovery to
`bundle/concepts/**/*.md`, so the three specification documents under
`docs/types/` are invisible to it. It reports six concepts where the bundle
holds nine. okf-parser walks the whole tree and narrows it with `.okfignore`,
so bundle layout is the author's choice rather than the tool's.

The differentiator is therefore not *relational access* — that is now contested
— but what the relations are computed over, and the contract they satisfy.

## Two disagreements about conformance

`google-okf` and `okf-retrieve` both fail this bundle. `google-okf` parses the
same nine concepts, finds the same single broken link, and exits non-zero. OKF
v0.2 states that an unresolved cross-link does not make a bundle non-conformant,
which is why okf-parser reports it as a warning and exits zero.

This is a specification interpretation difference rather than a defect in any
implementation, but the practical consequence is real: a bundle that passes one
gate fails another, so "OKF-conformant" is not yet a portable claim.

## Smaller observations

`okflint` counts files, not concepts: its audit reports ten indexed `.md` files
where the bundle holds nine concepts plus one reserved `index.md`. Both are
correct answers to different questions.

`okf-nav` reads bundles from `OKF_BUNDLES_DIR` rather than a path argument and
reported "No OKF bundles found" for a bundle passed positionally.

`okf-cli` is absent from this run. It installs an `okf` executable, and so does
`okf-retrieve`; installing both into one environment leaves only the one
installed last. The collision is an ecosystem fact worth recording, not a
benchmark defect, but it means the two cannot be measured side by side without
separate environments.

## What this does and does not show

It is not a performance comparison and must not be read as one. A linter that
reads a document and checks a rule will be faster than something that builds
tables and a graph, and that is the correct outcome rather than a defect.
Latency against these tools is deliberately not measured.

Rivals are given whatever configuration they require: `okflint` gets the
`okf-base.yaml` manifest it asks for, and the fixture carries `description`,
`generated` and `sources` frontmatter plus a declared `links:` list, because
okfquery reads links from frontmatter while okf-parser resolves them from the
Markdown body. Neither link model is wrong, so the fixture writes both.

Answering okfquery in SQL is answering it on its own terms, because SQL is its
interface. Post-processing another tool's output inside the harness would be
implementing the missing feature on its behalf, and is not done: a tool that
only prints a graph is recorded as not answering.
