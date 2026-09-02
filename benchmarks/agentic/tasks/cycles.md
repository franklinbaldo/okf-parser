---
type: BenchmarkTask
title: Link cycles
description: Identify cycles in a large mixed-type OKF graph
task_id: cycles
prompt: Identify every cycle in the link graph of the OKF bundle in ./bundle. Use short concept names. Write one canonical comma-separated cycle per line to answer.txt, sorted.
answer_kind: lines
fixture_kind: large-mixed-bundle
fixture_size: 1200
grader: cycles
---

# Link cycles

The graph contains multiple cycles crossing concept types rather than one toy triangle.
