---
type: Release Note
title: apply commits and new documents are written by the native binary
---

# apply commits and new documents are written by the native binary

Phase 3c of [RFC 0024](../../rfcs/0024-rust-native-core.md). Every `apply`
path computes its diff through DuckDB, the `--type/--field/--from/--to`
sugar included, so the DuckDB planning stays in Python until phase 4 and
everything after it moves to the binary:

- `__apply-snapshot` gives the planner the concepts and a digest of the
  bundle it read; `__apply-commit` snapshots again and refuses with
  `the bundle changed while apply was planning it` if anything the plan
  depended on changed. Concepts are compared by content, so a document
  edited and reverted in between does not count.
- Frontmatter is edited losslessly with `yaml-edit`: comments, quoting and
  layout outside the touched keys are kept. Documents the old ruamel-based
  check refused as not round-tripping (flow mappings, anchors, tags) are now
  edited instead of skipped. Every edit is parsed back and must equal the
  intended mapping, or the document is reported in `skipped_paths`.
- The candidate is fingerprinted, staged, validated and committed on the
  native write engine, the same one `edit` uses. Preview tokens are now
  `okf-apply-preview-v2-sha256:`; a v1 token no longer matches.
- `dumps` and `import` render new documents natively (`serde_yaml_ng`,
  through a batched `__render`): `type`, `title` and `description` first,
  every other key sorted, and the output parsed back before it is used.
  `import` therefore orders a row's fields canonically rather than by
  column order, and multi-line values use block scalars.

`okf_parser.write_support` and the `ruamel.yaml` dependency are gone.
