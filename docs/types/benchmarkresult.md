---
type: Spec
title: BenchmarkResult
description: A recorded measurement run, with the environment needed to judge whether it still applies
---

# BenchmarkResult

A `BenchmarkResult` concept records one measurement run. Concepts of this type
live in `benchmarks/results/` and are named by the date they were taken.

## Frontmatter

- `type` — always `BenchmarkResult`.
- `title` — what was measured.
- `description` — the comparison the run makes.

## Why the numbers are in the body

The measurements, the environment and the corpus description are prose in the
document body rather than typed frontmatter fields. That is a deliberate current
limit, not an oversight: a benchmark is only interpretable together with its
environment, and no consumer in this repository queries benchmark numbers
relationally today.

Should that change, this type is the most plausible first candidate for an
RFC 0006 declaration — a run has a date, a corpus size and a duration, and all
three are currently unqueryable.
