---
type: BenchmarkTask
title: No inbound links
description: Identify concepts with no inbound links in a large heterogeneous bundle
task_id: no-inbound
prompt: Identify every concept with no inbound link in the OKF bundle in ./bundle. Use short concept names and write one name per line to answer.txt, sorted.
answer_kind: lines
fixture_kind: large-mixed-bundle
fixture_size: 1200
grader: no-inbound
---

# No inbound links

The fixture includes intentional orphans across several types and directories.
