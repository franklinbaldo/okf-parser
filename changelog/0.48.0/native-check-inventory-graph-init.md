---
type: Release Note
title: check, inventory, graph and init run in the native binary
---

# check, inventory, graph and init run in the native binary

Phase 3b of [RFC 0024](../../rfcs/0024-rust-native-core.md).

The binary now answers `check`, `inventory`, `graph` and `init` itself, as
CLI commands and as MCP tools, without starting Python. Their JSON output and
exit status are unchanged. That covers `check --classify`, `--require-spec`
and `--normative-spec`: the `OKF010` and `OKF011` type-specification rules
and the specification scaffold of `init` are now in
`okf-engine/src/specs.rs`, pinned against the Python slug by the shared
`conformance/type-spec-slugs.json`. Two variants still need DuckDB and are
passed to Python until phase 4: `check --relational-schema` and
`init --infer-schema`.

The Python paths these replace are gone: `okf_parser.classification`, the
type-specification checks in `okf_parser.type_specs`, the specification
scaffold in `okf_parser.spec_scaffold`, and the `inventory`/`graph` handlers
in the service, CLI and MCP bridge. `validate_path` and `check_bundle` now
ask the binary (`__check`) and add only the DuckDB relational diagnostics.

Behavior changes:

- `ConceptRecord.frontmatter_json` from `load_bundle` is now the binary's
  spelling, the same one TypeScript and the DuckDB extension already
  received: compact JSON with keys in UTF-16 order, as the parsed digest is
  computed. It was re-serialized in Python with `json.dumps(sort_keys=True)`
  (spaced, code-point order), so Python alone disagreed with the other
  surfaces. The parsed value is unchanged.
- `check --classify` no longer lists Markdown under `node_modules/` as
  `ignored`: like `.git`, it is never a candidate.
- A specification template without `{slug}` is refused even for a bundle
  with no typed concepts, instead of only once a type needed it.
- A bundle root that does not exist reports
  `bundle root is not a directory: <path> (<reason>)`.
- `init` creates each specification stub exclusively (staged, then
  hard-linked into place): a document another writer created after the plan
  is left untouched instead of being overwritten.
- `ParsedDocument.frontmatter_json` uses the same compact, UTF-16-ordered
  spelling, so every Python record agrees with the binary.
- `okf-parser --help` lists every public command. The binary declares the
  whole command line and passes the Python-backed commands' arguments,
  `--help` included, through unparsed.
- `check --relational-schema` validates relations against the same bundle
  snapshot the rest of the report describes.
