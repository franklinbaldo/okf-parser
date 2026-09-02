---
type: BenchmarkResult
title: OKF ecosystem capability matrix
description: Which questions each published OKF tool answers on one shared fixture
---

# Capability matrix — 2026-09-02

Environment: Windows 11 x86_64, Python 3.12, measuring **`okf-parser` 0.45.6 as
published on PyPI** against `kbforge-okfquery` 0.1.0, `okf-generator` 0.1.53,
`okflint` 0.4.0, `okf-nav` 0.1.0, `okf-cli` 0.6.1, `okf-retrieve` 0.1.1,
`okf-schema` 0.12.0 and `google-okf` 0.1.3. Reproduce with:

```bash
uv run --script benchmarks/capability_matrix.py
```

The harness provisions one virtual environment per rival and installs each from
PyPI itself, so the run needs no prepared environment and no rival can shadow
another.

## This supersedes a first run that was not fair

The first version of this result recorded `okf-nav` as answering nothing. That
was wrong. `okf-nav` reads bundles from an `OKF_BUNDLES_DIR` environment
variable, the harness passed the bundle as a positional argument, and the tool
correctly reported that it found no bundles. Recording that as an incapability
measured the harness, not the tool.

The same run also gave `okflint` the `okf-base.yaml` manifest it requires, which
made the omission an inconsistency rather than a uniform limitation. Shown a
directory of bundles the way it expects, `okf-nav` answers two of the eight
questions correctly.

Two further corrections came with it. `okf-generator` was not measured at all,
despite generating OKF v0.2 bundles and advertising `lookup`, `diff --impact`,
`visualize` and `mcp`. And `okf-cli` could not be measured, because it shares
the executable name `okf` with `okf-retrieve` and `okf-generator`; one
environment per rival is what made it measurable.

## Result

| question | okf-parser | okfquery | okf-generator | okf-nav | okflint | okf-cli | okf-retrieve | okf-schema | google-okf |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| conformant? | yes | yes | — | — | yes | yes | disagrees | — | disagrees |
| concept count | yes | scope | **yes** | **yes** | files | ran | — | — | — |
| count per type | yes | scope | **yes** | **yes** | — | — | — | — | — |
| no inbound link | yes | scope | — | — | — | — | — | — | — |
| cycles | yes | **yes** | — | — | — | — | — | — | — |
| unresolved links | yes | **yes** | — | — | — | — | — | — | — |
| types without spec | yes | scope | — | — | — | — | — | — | — |
| impact of deleting `a` | yes | **yes** | model | — | — | — | — | — | — |
| | **8/8** | **4/8** | **2/8** | **2/8** | 1/8 | 1/8 | 0/8 | 0/8 | 0/8 |

`scope` means the tool answered correctly for the documents it looks at but does
not look at the whole bundle. `model` means it answers a different question.
`ran` means it produced readable output this harness does not grade. A dash means
no subcommand could answer.

## What the tools actually contest

**`kbforge-okfquery` answers the graph questions.** Cycles, impact analysis and
unresolved-link counting are not exclusive to okf-parser: recursive SQL over the
DuckDB connection it hands back reaches all three, and its impact answer for
deleting `a` is `b, c, d`, identical to okf-parser's. Any claim that only this
project can query a bundle relationally is false.

All four of its disagreements have one cause: it hard-codes discovery to
`bundle/concepts/**/*.md`, so the three specification documents under
`docs/types/` are invisible and it counts six concepts where the bundle holds
nine.

**`okf-generator` and `okf-nav` both count correctly and by type**, over the
whole tree, which is more than the first run credited either with. Neither
reaches the link-graph questions. `okf-generator`'s `diff --impact` is impact
over `Dependency` concepts produced by code indexing rather than over authored
links, so on this fixture it reports nothing affected — a different question,
not a wrong answer.

What remains distinctive is narrower than "relational access": whole-tree
discovery under the author's own layout, the declared relational contract, and
the link-graph questions no other tool reached.

## Two disagreements about conformance

`google-okf` and `okf-retrieve` both fail this bundle. They parse the same
concepts, find the same single unresolved link, and exit non-zero. OKF v0.2
states that an unresolved cross-link does not make a bundle non-conformant,
which is why okf-parser reports it as a warning and exits zero. A bundle that
passes one gate fails another, so "OKF-conformant" is not yet a portable claim.

`okflint` counts files, not concepts: ten indexed `.md` files where the bundle
holds nine concepts plus one reserved `index.md`. Both are correct answers to
different questions.

## What this measures, and what it does not

It measures the **published** okf-parser, not the working tree: the benchmark is
a PEP 723 script whose dependency resolves from an index. The report records the
version and import location it actually loaded, which is how the run that
produced this file was caught measuring 0.45.5 while 0.45.6 was current. Reading
this as a gate on the current checkout would be wrong; it is an ecosystem
comparison between released artifacts.

It is not a performance comparison and must not be read as one. A linter that
reads a document and checks a rule will be faster than something that builds
tables and a graph, and that is the correct outcome rather than a defect.

Every rival is given the configuration it requires: `okflint` its manifest,
`okf-nav` its `OKF_BUNDLES_DIR`, `okf-generator` a second bundle to diff
against, and the fixture carries `description`, `generated` and `sources`
frontmatter plus a declared `links:` list, because okfquery reads links from
frontmatter while okf-parser resolves them from the Markdown body. Neither link
model is wrong, so the fixture writes both.

Answering okfquery in SQL is answering it on its own terms, because SQL is its
interface. Post-processing another tool's output inside the harness would be
implementing its missing feature on its behalf, and is not done: a tool that
only prints a graph is recorded as not answering.
