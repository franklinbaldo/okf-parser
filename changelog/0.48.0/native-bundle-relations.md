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

The MCP `sql` tool deliberately does not expose this switch: ordinary MCP SQL
keeps its read-only/no-external-access contract, while trusted relation SQL has
the broader effect boundary documented by RFC 0021.
