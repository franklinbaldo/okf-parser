---
type: Release Note
title: Build observability and engine fast path
---

- Add an engine-only Rust fast path that tests and lints `okf-engine` without fetching DuckDB or compiling the SQL/MCP facade.
- Record Cargo wall-time metadata and preserve `cargo --timings` reports for both the fast path and the existing full Rust gate, so future SQL/MCP modularization can be based on measured build costs.
