---
type: Spec
title: BenchmarkTask
description: One deterministic problem handed to the benchmark agent
---

# BenchmarkTask

A `BenchmarkTask` is one problem in the agentic capability round. Task data is
part of the OKF bundle; the runner must never maintain a parallel JSON registry.

Fields:

- `task_id` — stable task identifier.
- `prompt` — the problem statement handed to the agent.
- `answer_kind` — one of `bool`, `int`, `strings`, `cycles`, or `counts`.
- `expected_bool` — expected boolean when `answer_kind=bool`.
- `expected_int` — expected integer when `answer_kind=int`.
- `expected_strings` — canonical string list for list/count/cycle answers.

For `counts`, entries use `name=count`. For `cycles`, each cycle is a comma-separated
canonical sequence such as `a,b,c`. This keeps the authored oracle typed and
queryable without embedding JSON inside frontmatter.
