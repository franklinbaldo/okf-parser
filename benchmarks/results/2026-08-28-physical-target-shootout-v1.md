---
type: Benchmark
title: Workload-specific physical target shootout — 2026-08-28
description: Same-host workload comparison of canonical in-memory DuckDB, indexed SQLite, ZSTD Parquet, and Arrow IPC with parity gates before timing.
---

# Workload-specific physical target shootout — 2026-08-28

## Verdict

There is no universal physical winner. The benchmark supports the RFC 0014 design: choose a derived representation only for a demonstrated workload.

- **SQLite clears the runtime gate for keyed navigation.** At 50k concepts it is about **51.9× faster for exact `concept_id` lookup** and **79.6× faster for outgoing adjacency** than the canonical in-memory DuckDB baseline. Its median materialization cost is 202.2 ms, which breaks even after about **327 point lookups** or **170 adjacency lookups**.
- **Parquet does not clear a runtime-query gate.** It is exceptionally compact in this synthetic, highly compressible fixture, but its 50k median scan is about **5.9% slower** than canonical in-memory DuckDB and its point/adjacency paths are much slower. Keep it as a possible persisted analytical/export target only when a real persistence workload justifies it.
- **Arrow IPC does not clear a query gate, but strongly clears an interchange primitive benchmark.** Mapping and reading the two 50k IPC files into Arrow takes about **0.279 ms**, versus **9.606 ms** for reading the equivalent Parquet files into Arrow — about **34.4× faster**. Querying the registered Arrow tables through DuckDB is still slower than querying the canonical in-memory DuckDB tables. Keep Arrow IPC as a future interchange/warm-start candidate, not a query backend.

Therefore this PR **does not add Parquet or Arrow product materializers**. The only product target justified so far remains the SQLite hot-path materialization from #184.

## Method

The run used three independent GitHub-hosted Ubuntu 24.04.4 runners (`eastus`, `westus3`, and `westus`) on runner image `20260823.283.1`. Each repetition used 1,000 deterministic query keys, five timed rounds, one warmup, and corpus sizes of 1k, 10k, and 50k concepts with two links per concept.

Before any timing, every target had to equal canonical DuckDB for:

1. exact point lookup;
2. outgoing adjacency;
3. grouped scan aggregate; and
4. ordered filtered projection.

All three repetitions passed the parity gate. The measured commit was `d3b6ba2fd17788423ee4c8c4ad5d03721083883a`; GitHub Actions run `33226543301` preserves the execution evidence. The JSON companion preserves all three raw repetitions and the cross-run medians.

## Cross-run medians

### Exact lookup, µs/query

| concepts | DuckDB | SQLite | Parquet | Arrow IPC |
| ---: | ---: | ---: | ---: | ---: |
| 1k | 342.262 | **10.073** | 638.839 | 883.948 |
| 10k | 398.414 | **10.463** | 982.212 | 932.947 |
| 50k | 632.399 | **12.190** | 2,651.323 | 1,091.031 |

### Outgoing adjacency, µs/query

| concepts | DuckDB | SQLite | Parquet | Arrow IPC |
| ---: | ---: | ---: | ---: | ---: |
| 1k | 595.907 | **11.653** | 920.735 | 1,128.650 |
| 10k | 713.809 | **12.047** | 1,475.369 | 1,236.518 |
| 50k | 1,206.629 | **15.168** | 3,900.912 | 1,548.459 |

### Grouped scan, ms

| concepts | DuckDB | SQLite | Parquet | Arrow IPC |
| ---: | ---: | ---: | ---: | ---: |
| 1k | 1.114 | **0.250** | 1.406 | 1.471 |
| 10k | **1.728** | 2.642 | 1.912 | 2.100 |
| 50k | **3.719** | 15.595 | 3.937 | 4.830 |

The 1k SQLite result is fixed-overhead territory. By 10k and 50k the expected set-oriented shape appears: canonical in-memory DuckDB is fastest. At 50k Parquet is close, but it does not beat DuckDB in the cross-run median.

### Ordered filtered projection, ms

| concepts | DuckDB | SQLite | Parquet | Arrow IPC |
| ---: | ---: | ---: | ---: | ---: |
| 1k | 0.640 | **0.150** | 0.998 | 1.265 |
| 10k | 1.586 | **1.117** | 1.985 | 2.129 |
| 50k | 2.697 | **1.135** | 3.927 | 3.000 |

This particular projection (`Type3`, `ORDER BY concept_id`, `LIMIT 1000`) can exploit SQLite's existing `concept_id` index and stop early. It is evidence about this query shape, not evidence that broad filters should be rerouted to SQLite. Earlier benchmark evidence showed the opposite result for another type-filter shape, so planner routing must remain operation/plan specific and benchmark-driven.

## Materialization cost

| concepts | SQLite | Parquet | Arrow IPC |
| ---: | ---: | ---: | ---: |
| 1k | 13.49 ms | 5.07 ms | **3.97 ms** |
| 10k | 41.17 ms | 16.74 ms | **9.73 ms** |
| 50k | 202.20 ms | 67.70 ms | **36.93 ms** |

Arrow IPC is cheapest to produce in this fixture; Parquet pays compression cost; SQLite pays table creation plus B-tree index construction. Those costs are acceptable only when amortized by the corresponding workload.

## Physical size

| concepts | SQLite | Parquet ZSTD | Arrow IPC |
| ---: | ---: | ---: | ---: |
| 1k | 397,312 B | **8,299 B** | 273,820 B |
| 10k | 3,710,976 B | **54,700 B** | 2,746,196 B |
| 50k | 18,956,288 B | **172,447 B** | 13,867,860 B |

At 50k the synthetic Parquet artifact is about **110× smaller than SQLite** and **80× smaller than Arrow IPC**. This ratio must not be extrapolated to real OKF corpora: the benchmark deliberately uses regular, repetitive identifiers and values that compress unusually well. The valid conclusion is qualitative — Parquet is the strongest compact persisted representation in this fixture — not that production bundles will compress by 110×.

## Interchange load into Arrow

| concepts | Parquet → Arrow | Arrow IPC mmap → Arrow | IPC speedup |
| ---: | ---: | ---: | ---: |
| 1k | 1.483 ms | **0.215 ms** | 6.9× |
| 10k | 3.103 ms | **0.250 ms** | 12.4× |
| 50k | 9.606 ms | **0.279 ms** | 34.4× |

This is the clearest Arrow result. It justifies retaining Arrow IPC as a candidate when a concrete cross-process interchange, cache image, or warm-start workload appears. It does not justify introducing it now as a public or planner-visible backend.

## Decisions

1. Keep #184's SQLite materializer and hot-path role.
2. Do not implement a Parquet runtime target from this benchmark. Revisit only for a concrete compact-persistence/export workload with its own acceptance gate.
3. Do not implement Arrow IPC as a query target. Revisit for a concrete interchange/cache/warm-start workload; compare end-to-end startup, not only `read_all()`.
4. Keep DuckDB as the direct relational/query baseline and transformation engine.
5. Keep physical target choice private and workload-shaped; no public `target=` or engine selector follows from these results.

The result is intentionally a frontier, not a winner: **SQLite for keyed navigation; DuckDB for direct relational work; Parquet for compact persistence if needed; Arrow IPC for fast interchange if needed.**
