---
type: BenchmarkTask
title: Conformance
description: Determine whether a large heterogeneous fixture bundle is conformant
task_id: conformant
prompt: Determine whether the OKF bundle in ./bundle is conformant. Write only true or false to answer.txt.
answer_kind: scalar
fixture_kind: large-mixed-bundle
fixture_size: 1200
grader: conformant
---

# Conformance

The fixture contains more than one thousand concepts across several types and directories.
