---
type: Release Note
title: Bundle.sql(), okf-parser sql and the MCP sql tool
---

# Bundle.sql(), okf-parser sql and the MCP sql tool

Phase 4b of [RFC 0024](../../rfcs/0024-rust-native-core.md). A bundle now
answers SQL, run by the binary over the tables `okf-parser duckdb` exports:
`concepts`, `links`, `reserved` and `diagnostics` and, with a spec template,
one typed table per declared type, all on the search path.

- `Bundle.sql(query, spec_template=None, limit=None)` returns a `SqlResult`
  (`columns` with their DuckDB types, `rows`, `truncated`, `to_dicts()`),
  computed from the snapshot the `Bundle` holds. Values come back as Python
  values by column type: `Decimal`, `date`, `datetime` (UTC-aware for
  `TIMESTAMP WITH TIME ZONE`), `bytes`, parsed `JSON`, and nested lists,
  structs and maps.
- `okf-parser sql PATH "QUERY"` prints the same answer as JSON, and the
  read-only MCP `sql` tool answers it with at most 1000 rows. Graph questions
  become queries: `WITH RECURSIVE` over `links` answers reachability.
- The query cannot read files, reach the network, load extensions or change
  settings, and is exactly one query; anything else raises `SqlError` (a
  `ValueError`) and is a command or tool error, reported against the query's
  own text.

`Bundle.compile_types()` and `TypedRelations` are removed with their ibis
tables: query the typed tables through `Bundle.sql(..., spec_template=...)`.
The GraphQL adapter reads declared values that way too.
