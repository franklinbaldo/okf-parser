---
type: Release Note
title: transparent hot-path accelerators proposal
---

- Propose RFC 0013: keep DuckDB/Ibis as the canonical relational interface while allowing private, snapshot-keyed accelerators for proven hot operations such as exact identity lookup and immediate adjacency. Physical plans remain invisible to public semantics, consume only canonical relations, require parity/delete-rebuild equivalence, and must justify build cost, memory and end-to-end break-even before routing changes.
