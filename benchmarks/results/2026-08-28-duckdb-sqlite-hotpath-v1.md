---
type: BenchmarkResult
title: DuckDB to SQLite physical materialization
---

# DuckDB to SQLite physical materialization

This benchmark tests one workload-specific physical target derived from canonical relations. DuckDB performs cross-database `CREATE TABLE AS SELECT` into SQLite; SQLite adds indexes on `concept_id`, `source_id`, and `target_id`. No authored OKF is reparsed and no semantic rule exists in the SQLite layer.

The benchmark uses synthetic relation tables with two outgoing links per concept. Before timing, point lookup and outgoing-adjacency results must match between DuckDB, file-backed SQLite, and an SQLite `:memory:` copy. Each query timing is the median of five batches of 1,000 deterministic keys.

Three complete GitHub-hosted Ubuntu 24.04 repetitions were preserved because materialization time showed substantial runner variance. In particular, the first 50k export measured 638.5 ms while the two replicas measured 186.3 ms and 192.2 ms. Conclusions therefore use medians rather than selecting the fastest run.

## 50k-concept median

| operation | DuckDB | SQLite file | SQLite memory | file speedup | memory speedup |
| --- | ---: | ---: | ---: | ---: | ---: |
| exact `concept_id` | 618.014 µs | 11.782 µs | 2.522 µs | 52.45× | 245.05× |
| outgoing adjacency | 1,163.033 µs | 14.283 µs | 3.978 µs | 81.43× | 292.37× |

The median DuckDB-to-SQLite materialization cost at 50k concepts / 100k links was **192.228 ms**. Copying the finished SQLite file into `:memory:` added a median **13.677 ms**.

Against remaining on DuckDB, the derived file-backed SQLite target pays for itself after roughly **318 point lookups** or **168 outgoing-neighbor lookups**. Including the extra memory-copy cost, the in-memory variant pays for itself after roughly **335 point lookups** or **178 outgoing-neighbor lookups**.

The additional `:memory:` step is a separate tradeoff. Relative to the already-indexed file-backed SQLite database, it needs roughly **1,477 point lookups** or **1,328 adjacency lookups** to recover its extra copy cost in this 50k fixture. Therefore this result supports SQLite for point and adjacency workloads, but does not make SQLite the universal physical representation and does not require every session to copy it into memory eagerly.

## Interpretation

The experiment validates DuckDB as a transformation and query engine over canonical relations, not as a representation that must itself be persisted or materialized. SQLite is the first proven derived target because its indexes suit point lookup and adjacency. Other workloads may justify different physical targets—for example Parquet for persisted columnar scans or Arrow for interchange/in-memory execution—without changing OKF semantics.

The product should therefore preserve a physical-materialization boundary rather than equating `accelerator` with SQLite. DuckDB can query canonical relations directly when appropriate and can materialize workload-specific targets when their physical properties are useful. A later planner may route operations by shape without exposing a public backend selector.

These are synthetic physical-layer timings, not end-to-end product speedups. Broad filters, joins, aggregation, arbitrary SQL, diff, and set-oriented impact remain DuckDB/native-set workloads unless separate evidence justifies another target or route.
