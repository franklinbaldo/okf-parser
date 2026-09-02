---
type: BenchmarkTask
title: Counts by type
description: Count concepts by type in the fixture bundle
task_id: type-counts
prompt: Count concepts by type in the OKF bundle in ./bundle. Write one line per type to answer.txt using Type=count, sorted by type name.
answer_kind: counts
expected_strings:
  - Ledger=1
  - Record=2
  - Service=3
  - Spec=3
---

# Counts by type

Count all concepts grouped by their declared type.
