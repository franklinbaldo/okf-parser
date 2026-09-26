---
type: Release Note
title: The write engine and body edits run in the native binary
---

# The write engine and body edits run in the native binary

Phase 3a of [RFC 0024](../../rfcs/0024-rust-native-core.md). The conflict-safe
write protocol shared by every writer (snapshot, stage and validate a
candidate bundle, recheck freshness, replace files atomically) now lives in
`okf-engine/src/write.rs`. `preview_concept_edit` and `write_concept_edit` run
entirely in the binary through a new `__edit` protocol command, with the same
arguments and result shape as before. `okf_parser.edit` is now a thin shell.

RFC 0024 also records two decisions: `import`, `init --infer-schema` and SQL
`apply` move with DuckDB (phase 4), and YAML writing will use maintained
libraries (`yaml-edit` for lossless edits, a serializer for new documents)
rather than hand-written emitters.
