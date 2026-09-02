---
type: BenchmarkResult
title: OKF ecosystem capability matrix
description: Which questions each published OKF tool can answer on one shared fixture
---

# Capability matrix — 2026-09-02

Environment: Windows 11 x86_64, Python 3.12, `okf-parser` 0.45.6 from PyPI,
`okflint` 0.4.0, `okf-cli` 0.6.1, `google-okf` 0.1.3, all installed from PyPI
into one throwaway environment. Reproduce with:

```bash
uv run --script benchmarks/capability_matrix.py
```

The fixture is nine concepts across four types, containing one link cycle, one
unresolved link, one type with no specification document, and three concepts
with no inbound link. Every expected answer is a property of those documents,
so the benchmark fails when okf-parser is wrong, not only when a rival is.

## Result

| question | okf-parser | okflint | okf-cli | google-okf |
| --- | --- | --- | --- | --- |
| Is the bundle conformant? | yes | yes | yes | **disagrees** |
| How many concepts does it contain? | yes | files, not concepts | yes | — |
| How many concepts of each type? | yes | — | — | — |
| Which concepts have no inbound link? | yes | — | — | — |
| Are there cycles in the link graph? | yes | — | — | — |
| How many links do not resolve? | yes | — | — | — |
| Which types in use have no specification? | yes | — | — | — |
| What breaks if concept `a` is deleted? | yes | — | — | — |

A dash means the tool exposes no subcommand that could answer the question. The
report records each rival's advertised command surface next to its verdicts, so
that claim is auditable rather than asserted:

- `okflint`: `audit`, `validate`, `validate-manifest`, `index`;
- `okf-cli`: `bundle`, `list`, `read`, `validate`;
- `google-okf`: `init`, `lint`, `produce`.

## Two findings worth keeping

`google-okf` **disagrees about conformance**. It parses the same nine concepts,
finds the same single broken link, and exits non-zero. OKF v0.2 states that an
unresolved cross-link does not make a bundle non-conformant, which is why
okf-parser reports it as a warning and exits zero. This is a specification
interpretation difference, not a defect in either implementation, but a bundle
that passes one gate will fail the other.

`okflint` counts **files, not concepts**: its audit reports ten indexed `.md`
files where the bundle holds nine concepts plus one reserved `index.md`. Both
numbers are correct answers to different questions.

## What this does and does not show

It shows a category difference. Every tool here validates; only okf-parser
answers questions *about the bundle as a whole*, because only it compiles the
bundle into relational tables and a link graph.

It is not a performance comparison and must not be read as one. A linter that
reads a document and checks a rule will be faster than something that builds
tables and a graph, and that is the correct outcome rather than a defect.
Latency against these tools is deliberately not measured.

`okflint` needs its own `okf-base.yaml` manifest, which the fixture provides.
A rival that cannot start is not evidence about capability.
