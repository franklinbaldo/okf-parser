---
type: Spec
title: Rival
description: A published tool that reads OKF bundles, recorded so the competitive landscape is queryable
---

# Rival

A `Rival` concept records one published tool that reads Open Knowledge Format
bundles. Concepts of this type live in `benchmarks/rivals/` and are named by
their distribution.

The type exists because this repository was wrong about its own competition.
The README named three tools — `okflint`, `okf-cli` and `google-okf` — and the
first capability matrix measured exactly those three, because that was the list
at hand. Searching both indexes afterwards found roughly 45 OKF packages on PyPI
and 90 on npm, including `kbforge-okfquery`, which answers the graph questions
this project treated as its own. A list living in prose went stale without
anyone noticing. A list living in the bundle can be queried, counted, and
checked against what the benchmark actually runs.

## Frontmatter

- `type` — always `Rival`.
- `title` — the distribution name.
- `description` — what the tool claims to do, in its own words where possible.
- `registry` — `pypi` or `npm`.
- `package` — the distribution name as published.
- `executable` — the command it installs, which is not always the package name.
- `version_measured` — the version the matrix last ran against; absent when the
  rival is recorded but not yet interrogated.
- `surface` — the subcommands it advertises, transcribed from its `--help`.
- `homepage` — where it is developed, when it declares one.
- `measured` — whether `benchmarks/capability_matrix.py` interrogates it today.

## Why the surface is data and the adapter is not

`surface` is recorded here because it is the evidence behind a verdict. When the
matrix reports that a rival cannot answer a question, the claim is auditable
only if the reader can see which subcommands existed at the time.

How to *interrogate* a rival — the argument vector for each question and the
function that reads an answer out of its output — stays in the benchmark script.
Those are code, including callables that no frontmatter can hold, and splitting
them across both would leave neither half readable.

## Why `measured` is a field rather than an inference

A rival with no adapter in the benchmark is not a gap in the data; it is a gap
in the measurement, and it should be visible as one. Recording rivals this
project has not yet interrogated is the point: `measured: false` is a queryable
backlog, where prose would simply omit them and read as complete.

## Declared columns

This type declares `docs/types/rival.schema.sql`, so the fields above are
typed relational columns rather than free frontmatter. `surface` is a
`VARCHAR[]`, which lets a query ask which rivals expose a given subcommand
without parsing prose.
