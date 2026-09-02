---
type: BenchmarkTask
title: Counts by type
description: Count concepts by type in a large mixed-type bundle
task_id: type-counts
prompt: Count concepts by type in the OKF bundle in ./bundle. Write one Type=count line per type to answer.txt, sorted by type name.
answer_kind: lines
fixture_kind: large-mixed-bundle
fixture_size: 1200
grader: type-counts
---

# Counts by type

The fixture mixes several domain types, specifications and deliberately unspecified types.
