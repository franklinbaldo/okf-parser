---
type: BenchmarkTask
title: JSON to OKF conversion
description: Convert 1200 raw JSON records into a valid OKF bundle without losing data
task_id: json-to-okf
prompt: Convert all 1200 records in ./input.json into the OKF bundle ./converted/. Preserve every source field and value faithfully. Do not leave JSON as the final representation. The resulting bundle must be valid OKF Markdown.
answer_kind: artifact
fixture_kind: json-records
fixture_size: 1200
grader: json-to-okf
---

# JSON to OKF conversion

The input is deliberately large enough that a one-off manual rewrite is not a meaningful strategy.
The grader validates the generated bundle and compares every source record field-by-field against the
corresponding OKF concept.
