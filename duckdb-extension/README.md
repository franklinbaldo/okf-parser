# OKF DuckDB extension

Experimental loadable DuckDB extension from RFC 0010.

The extension is intentionally kept outside the root Cargo workspace: the normal `okf-engine`/`okf-core` lockfile and release graph stay unchanged while DuckDB's Rust loadable-extension API remains experimental.

Current vertical slice:

```sql
LOAD './okf.duckdb_extension';
FROM okf_concepts('./knowledge');
```

`okf_concepts()` delegates parsing and validation semantics to `okf-engine`; it does not embed or spawn a second DuckDB and emits rows directly into DuckDB data chunks.
