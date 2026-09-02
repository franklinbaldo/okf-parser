---
type: BenchmarkTask
title: No inbound links
description: Identify concepts with no inbound links
task_id: no-inbound
prompt: Identify every concept with no inbound link in the OKF bundle in ./bundle. Use short concept names. Write one name per line to answer.txt, sorted.
answer_kind: strings
expected_strings:
  - d
  - e
  - f
  - record
  - service
  - spec
---

# No inbound links

Identify all concepts that no other concept links to.
