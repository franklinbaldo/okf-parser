---
type: BenchmarkTask
title: Transitive deletion impact
description: Determine transitive impact of deleting a concept from the middle of a large mixed-type graph
task_id: impact-delete-middle
prompt: Determine every existing concept transitively impacted if the designated concept named in ./target.txt is deleted from ./bundle. Use short concept names and write one per line to answer.txt, sorted.
answer_kind: lines
fixture_kind: large-mixed-bundle
fixture_size: 1200
grader: impact-delete-middle
---

# Transitive deletion impact

The target sits inside a long dependency chain with cross-type branches, so direct backlinks are not sufficient.
