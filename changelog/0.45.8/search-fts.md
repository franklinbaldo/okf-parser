---
type: Release Note
title: Add optional local DuckDB FTS search
---

- Prefer DuckDB FTS/BM25 for lexical search only when the `fts` extension is already installed and locally loadable.
- Disable DuckDB known-extension autoinstall and autoload before extension discovery, never issue `INSTALL`, and fall back to `builtin_lexical_v1` on any local FTS failure.
- Build the FTS table and index only in an in-memory DuckDB connection for the current search invocation; no persistent sidecar or canonical Markdown state is changed.
