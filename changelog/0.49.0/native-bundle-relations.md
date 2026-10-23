---
type: Release Note
title: Bundle relation SQL executes natively over typed tables
---

# Bundle relation SQL executes natively over typed tables

`Bundle.sql(..., relations=True)` and `okf-parser sql --relations` now run the
optional trusted bundle-root `okf.relations.sql` after RFC 0006 materializes
`okf_types` in the same DuckDB connection. The program publishes under the
reserved `okf_relations` schema, which is then available to the read-only
query. Relation execution requires a spec template and remains opt-in.

Relation views are evaluated at query time, after the lockdown, so a program
that reads external data must materialize it with `CREATE TABLE ... AS`; a
published view that only binds with file or network access is rejected with an
error naming the view instead of failing later when someone queries it.

The MCP `sql` tool does not expose this switch. Its `spec_template` already
runs each trusted `.schema.sql`, but on a separate declaration connection; the
difference is that relation SQL runs on the query connection itself, before it
is locked down. Keeping that out of MCP preserves the RFC 0008 read-only
contract of the connection MCP queries.
