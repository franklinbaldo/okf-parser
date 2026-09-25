---
type: Release Note
title: Recovery test for the DuckDB projection
---

# Recovery test for the DuckDB projection

Added a test proving the `.duckdb` export can be deleted and rebuilt from the
source Markdown bundle with byte-identical results, and that rebuilding never
touches the source files. Generated database projections must stay disposable
derivatives of the bundle, never an accidental source of truth.
