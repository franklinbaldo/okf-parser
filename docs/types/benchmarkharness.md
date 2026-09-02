---
type: Spec
title: BenchmarkHarness
description: One pinned agent harness configuration for an agentic benchmark round
---

# BenchmarkHarness

A `BenchmarkHarness` records the agent shell used for a benchmark round.
The first round intentionally has exactly one enabled harness so the comparison
varies the OKF tool, not the agent shell.

Fields:

- `harness_id` — stable identifier.
- `package` — published package name.
- `version` — exact pinned version.
- `provider` — model provider used by the round.
- `enabled` — whether this harness participates in the current round.

Future rounds may add Kilo, Ori-launched harnesses or others by adding concepts,
without changing the task or rival registries.
