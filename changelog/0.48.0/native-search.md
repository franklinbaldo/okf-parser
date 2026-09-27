---
type: Release Note
title: search runs in the binary, as a command, an MCP tool and Bundle.search()
---

# search runs in the binary, as a command, an MCP tool and Bundle.search()

Phase 4d of [RFC 0024](../../rfcs/0024-rust-native-core.md). RFC 0016 search
moves to `okf_engine::search` and gets a surface:

- `okf-parser search PATH QUERY` answers `location<TAB>snippet` rows, with
  `--mode literal`, `--limit`, `--context`, `--type`, `--path`, `--detail
  score|full` and `--exclude`.
- The read-only MCP `search` tool, in the default profile, answers compact rows
  as text and `full` results as structured content.
- `Bundle.search()` is the method form of `search_bundle()`, which now runs on
  the binary over the snapshot the `Bundle` holds.

The built-in BM25 scorer and its results are unchanged; the shared conformance
cases pass as before.

**Removed.** The optional DuckDB FTS path: `fts` is not linked into DuckDB's
release builds and loading it needs `INSTALL`, which search must never run, so
it only answered where the extension had been installed by hand, with scores
that differed from every other machine. `okf_parser.materialization`
(`materialize_sqlite_hot`, `open_sqlite_memory_copy`) and
`okf_parser.body_lines` are removed too.

The `duckdb` Python package is no longer a dependency: nothing in the package
imports it. Read an exported `.duckdb` file with any DuckDB client.
