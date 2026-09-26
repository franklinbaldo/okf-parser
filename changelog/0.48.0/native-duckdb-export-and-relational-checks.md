---
type: Release Note
title: duckdb, relational checks and starter schemas run in the binary
---

# duckdb, relational checks and starter schemas run in the binary

Phase 4a of [RFC 0024](../../rfcs/0024-rust-native-core.md). The binary now
carries DuckDB itself, through the `duckdb` crate in a new `okf-db` crate, and
answers these without Python:

- `okf-parser duckdb` and the MCP `duckdb_export` tool. `--help` now
  documents the command. The export runs in one transaction: a refused
  export, a typed-table collision included, leaves nothing behind.
- `check --relational-schema` and the MCP `check` tool with
  `relational_schema`. `OKF020`-`OKF022` are native diagnostic codes.
- `init --infer-schema` and the MCP `init_*` tools with `infer_schema`,
  from the same read of the bundle as the specification scaffold.

`okf-parser duckdb` now writes `knowledge.duckdb` by default, not
`okf.duckdb`. DuckDB names a database after its file, so in `okf.duckdb` a
query for `okf.concepts` was ambiguous between the database `okf` and the
schema `okf` and failed. Pass the database name to keep the old file.

Messages are clearer. Relational diagnostics name the type, the key and
its value (``` `Book` isbn = "1" is already used by a.md (constraint
`Book_isbn_pkey`) ```) instead of Python tuple reprs, and a refused export
says to pass `--overwrite` or choose another `--schema`.

In the Python API, `okf_parser.duckdb.attach_okf` is removed: exporting
into a caller's own connection has no counterpart in the binary. Use
`okf_parser.service.export_duckdb(path, database)`, which raises
`okf_parser.service.BundleExportError` on a collision. `validate_relations`
and `okf_parser.spec_scaffold` are removed too: `validate_path(...,
relational_schema=...)` and `init_bundle(..., infer_schema=True)` answer
from the binary. `parse_declared_schema` and `parse_relational_schema` keep
their signatures and run their SQL in the binary.

Starter schemas no longer type a field `BOOLEAN` because its values are
`1`/`0` or `t`/`f`, which DuckDB also casts to booleans: only `true` and
`false`, in any case, make a boolean, so a `1`/`0` field is `BIGINT`.

Wheels ship DuckDB's own prebuilt library (with its JSON, Parquet and ICU
extensions) next to the `okf-parser` executable, pinned by SHA-256 and
checked on every platform before upload; the npm native package carries it
too. Building from source compiles DuckDB in instead. Typed-table timestamps
are still cast in UTC, so a `TIMESTAMPTZ` without an offset means the same
instant in every session.
