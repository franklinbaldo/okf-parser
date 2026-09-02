---
type: Spec
title: BenchmarkRun
description: Immutable evidence for one agentic benchmark trial
---

# BenchmarkRun

A `BenchmarkRun` is the canonical persisted evidence for one agentic trial.
It is authored by the runner after the trial completes and stored as OKF Markdown.
JSON may appear transiently inside a harness protocol, but published benchmark
evidence is never a JSON registry or JSONL database.

Fields:

- `run_id` — unique run identifier.
- `task_id` — benchmark task exercised.
- `tool_id` — rival id or `baseline-none`.
- `tool_package`, `tool_version`, `tool_executable` — pinned tool identity.
- `tool_used` — whether the observed executable was actually invoked.
- `harness_id`, `harness_version` — fixed harness identity.
- `model`, `provider` — model configuration.
- `repetition` — independent repetition number.
- `wall_seconds`, `budget_seconds` — measured duration and allowed wall-clock budget.
- `status` — `success` or `failure`.
- `graded` — deterministic grader result.
- `failure_class` — preserved failure taxonomy when unsuccessful.
- `answer` — canonical textual representation of the produced answer.
- `expected` — canonical textual representation of the oracle.
- `started_at`, `finished_at` — UTC timestamps.
- `transcript_path`, `tool_log_path` — artifact references.

The Markdown body may carry a short human-readable account, but all fields used
for aggregation live in typed frontmatter.
