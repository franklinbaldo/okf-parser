---
type: Release Note
title: apply runs in the binary, and its contract is simpler
---

# apply runs in the binary, and its contract is simpler

Phase 4c of [RFC 0024](../../rfcs/0024-rust-native-core.md). `okf-parser
apply`, the MCP `apply_preview`/`apply_write` tools and `apply_bundle()` are
planned and committed by the binary in one request; the Python side is a
pydantic wrapper.

The planner used to accept a narrow grammar (leading `ALTER TABLE`s and one
`UPDATE`, on one type) and reconstruct intent from it. Now the final state is
the truth:

- Each concept type is a table. The script is any sequence of statements,
  run whole in a sandboxed in-memory DuckDB: no files, network, extensions or
  setting changes. Several types can change in one script.
- A changed value sets the field, as text. `NULL` where a value existed, or a
  dropped or renamed column, removes the field. `SET x = NULL` on an explicit
  `x: null` keeps it.
- Rows, the `__okf_*` columns, declared fields (with `--spec-template`) and
  structured (list or mapping) fields are not writable; a script that touches
  them is refused with the reason and nothing is written.

**Breaking.** A retyped column (`ALTER COLUMN ... TYPE INTEGER`) is no longer
refused: its values are written as text. Declared fields are read-only under
`--spec-template`; run apply without the template to edit them as text.
`apply_preview` is now annotated read-only and idempotent. The hidden
`__apply-snapshot`/`__apply-commit` commands are replaced by `__apply`.

`import` reads its source through the binary too (`__read-source`), every
value as DuckDB's own `VARCHAR` cast of it: booleans import as `true`/`false`
rather than Python's `True`/`False`, and lists as DuckDB spells them.

ibis, pyarrow, pandas and numpy are no longer dependencies.
