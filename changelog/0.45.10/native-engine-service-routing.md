---
type: Release Note
title: Route read services through the automatic native engine
---

# Route read services through the automatic native engine

Python service reads now use the same automatic engine selection as the public
`load_bundle` API. `check`, `inventory`, `graph`, and DuckDB export can use
the release-matched Rust engine when it is available instead of silently
falling back to the pure-Python bundle loader.

Validation still applies the existing Python-side type-spec and relational
rules after the bundle snapshot is loaded; only the bundle-loading engine
selection changes.
