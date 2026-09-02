---
type: BenchmarkTask
title: Unresolved links
description: Count unresolved links in a large heterogeneous OKF bundle
task_id: unresolved-links
prompt: Count every unresolved link in the OKF bundle in ./bundle. Write only the integer answer to answer.txt.
answer_kind: scalar
fixture_kind: large-mixed-bundle
fixture_size: 1200
grader: unresolved-links
---

# Unresolved links

Broken targets are distributed across several concept types and directories.
