---
type: Spec
title: BenchmarkTask
description: One deterministic large-scale problem handed to the benchmark agent
---

# BenchmarkTask

A `BenchmarkTask` is one problem in the agentic capability round. Task data is
part of the OKF bundle; the runner must never maintain a parallel JSON registry.

The first round deliberately avoids toy fixtures. A task should contain enough
data that manually reading the files is materially worse than using a useful
OKF tool. The scale is declared, reproducible and identical for every rival.

Fields:

- `task_id` — stable task identifier.
- `prompt` — problem statement handed unchanged across rival conditions.
- `answer_kind` — shape of the deliverable (`scalar`, `lines`, `artifact`).
- `fixture_kind` — deterministic fixture generator name.
- `fixture_size` — primary scale parameter.
- `grader` — deterministic grader name.

The grader derives the oracle from the generated fixture rather than storing
large expected answer lists in frontmatter. This prevents the benchmark bundle
from containing the answer in a form the agent can simply discover.

For the initial round, query/graph tasks should use at least one thousand
concepts or records unless the task intrinsically requires a different scale.
The JSON-to-OKF conversion task converts at least one thousand source records,
not a single demonstration object.
