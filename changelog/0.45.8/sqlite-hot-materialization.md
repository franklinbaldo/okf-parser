---
type: Release Note
title: workload-specific SQLite materialization
---

- Add RFC 0014 and a measured SQLite physical-materialization target derived from canonical relations for exact identity lookup and immediate adjacency. DuckDB remains the relational/transformation baseline; SQLite is private, disposable derived state with parity tests and explicit build/break-even measurements rather than a new public backend semantic.
