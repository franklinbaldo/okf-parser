---
type: BenchmarkTask
title: Types without specification
description: Identify in-use concept types without a specification in a large heterogeneous bundle
task_id: types-without-spec
prompt: Identify every concept type in use in ./bundle that has no specification document. Write one type name per line to answer.txt, sorted.
answer_kind: lines
fixture_kind: large-mixed-bundle
fixture_size: 1200
grader: types-without-spec
---

# Types without specification

The fixture deliberately mixes specified and unspecified types across several directories.
