---
type: BenchmarkTask
title: Link cycles
description: Identify cycles in the fixture link graph
task_id: cycles
prompt: Identify every cycle in the link graph of the OKF bundle in ./bundle. Use short concept names. Write one canonical comma-separated cycle per line to answer.txt, sorted.
answer_kind: cycles
expected_strings:
  - a,b,c
---

# Link cycles

Identify all cycles in the fixture link graph.
