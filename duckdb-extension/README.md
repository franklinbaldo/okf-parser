# OKF DuckDB extension

Experimental loadable DuckDB extension from RFC 0010.

The extension is intentionally kept outside the root Cargo workspace: the normal `okf-engine`/`okf-core` lockfile and release graph stay unchanged while DuckDB's Rust loadable-extension API remains experimental.

Read-side SQL surface:

```sql
LOAD './okf.duckdb_extension';
FROM okf_concepts('./knowledge');
FROM okf_links('./knowledge');
FROM okf_reserved('./knowledge');
FROM okf_diagnostics('./knowledge');
```

All four functions delegate parsing, link resolution and diagnostics semantics to `okf-engine`. They do not embed or spawn a second DuckDB and emit rows directly into DuckDB data chunks. Bundle-level scan/configuration failures become SQL errors; document-level nonconformance stays queryable through `okf_diagnostics()`.
