---
type: Release Note
title: DuckDB and services use the resolved engine
---

- Route DuckDB materialization and application services through the same automatic engine resolution used by the public `load_bundle` API. When a compatible Rust core is installed, `attach_okf`, `export_duckdb`, `check`, `inventory`, `graph`, and `init` use it instead of silently falling back to the Python-only loader.
- Route the public `validate_path` API through that same engine selection while preserving relational-schema and type-spec validation semantics.
