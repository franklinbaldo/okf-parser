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

The native commit is also stricter than the Python one it replaces. Each write
stages into its own uniquely named sibling file. A multi-file commit rolls back
the files it already replaced if a later rename fails. The freshness recheck
and the commit run under an exclusive lock on `.okf-write.lock` at the bundle
root, so two concurrent writers can no longer both pass the recheck and
silently overwrite each other; the second reports a conflict. The lock file is
never treated as part of the bundle.

RFC 0024 also records two decisions: `import`, `init --infer-schema` and SQL
`apply` move with DuckDB (phase 4), and YAML writing will use maintained
libraries (`yaml-edit` for lossless edits, a serializer for new documents)
rather than hand-written emitters.

The Rust code as a whole now models its domain in types rather than strings,
with every public JSON, MCP and DuckDB shape unchanged:

- Engine, YAML, write and MCP failures are error enums (`LoadError`,
  `FrontmatterError`, `WriteError`, the MCP bridge errors) that keep their
  causes; they become text only in the protocol, CLI and MCP adapters.
  Diagnostic messages keep their exact wording.
- Diagnostic `code` and `severity`, reserved `filename` and link `origin` are
  enums that serialize to the same strings as before.
- Bundle paths are converted to text in one place (`BundlePath`), which
  refuses a path with no UTF-8 spelling instead of silently replacing bytes
  with U+FFFD, since that could give two files the same concept id. On Unix,
  a backslash in a file name is now kept as written, matching the Python
  engine's `as_posix()`.
- The canonical JSON behind `parsed_digest` and `frontmatter_json` is written
  from borrowed values, with no mapping clones and no per-comparison UTF-16
  buffers; a test pins it byte for byte against the previous writer.
