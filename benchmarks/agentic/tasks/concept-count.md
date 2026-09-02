---
type: BenchmarkTask
title: Concept count
description: Count concepts in a large heterogeneous OKF bundle
task_id: concept-count
prompt: Count every concept in the OKF bundle in ./bundle. Write only the integer answer to answer.txt.
answer_kind: scalar
fixture_kind: large-mixed-bundle
fixture_size: 1200
grader: concept-count
---

# Concept count

Count all concepts across the entire heterogeneous bundle, not only one conventional directory.
