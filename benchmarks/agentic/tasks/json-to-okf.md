---
type: BenchmarkTask
title: JSON to OKF conversion
description: Convert a raw JSON record into a valid OKF Markdown concept without losing data
task_id: json-to-okf
prompt: Convert ./input.json into ./output.md as a valid OKF Markdown concept. Preserve every source field and value faithfully. Do not leave the JSON as the final artifact. The resulting Markdown must pass okf-parser validation. Write no other final artifact.
answer_kind: artifact
fixture_kind: json_record
expected_output: output.md
artifact_validator: okf-conversion
---

# JSON to OKF conversion

The trial workspace contains `input.json` with a small structured record. The
agent must produce `output.md` as the canonical result.

The grader checks the artifact structurally rather than trusting the agent's
final prose:

- `output.md` exists;
- it is valid OKF Markdown;
- every source JSON field and value is preserved;
- no source field is silently discarded or semantically rewritten;
- the output is the Markdown concept, not a copied JSON file.

This task intentionally starts from JSON because the benchmark is measuring
whether an OKF tool helps an agent turn ordinary structured data into the format,
not because JSON is a source-of-truth format for the benchmark itself.
