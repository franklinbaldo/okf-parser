---
type: Release Note
title: workload-specific physical target benchmark
---

- Preserve the repeated same-host physical-target shootout across canonical DuckDB, indexed SQLite, ZSTD Parquet and Arrow IPC. The evidence promotes SQLite only for keyed navigation/adjacency, keeps DuckDB as the relational baseline, and records Parquet/Arrow as compact-persistence/interchange candidates rather than declaring a global backend winner.
