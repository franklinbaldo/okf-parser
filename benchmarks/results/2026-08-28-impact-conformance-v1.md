---
type: Benchmark
title: Impact conformance microbenchmark — 2026-08-28
---

# Impact conformance microbenchmark — 2026-08-28

Baseline for the executable RFC 0012 `impact` conformance kernel introduced in PR #180.

This benchmark is diagnostic, not a fixed CI latency gate. GitHub-hosted runners showed substantial cross-machine variance, so future accelerator comparisons should run baseline and candidate in the same process/run and compare relative speedups on the same corpus.

## Method

- Ubuntu 24.04 GitHub-hosted runners
- Python 3.12.3
- `benchmarks/impact_conformance.py`
- sizes: 1, 10, 100, 1,000, 10,000, 50,000 records
- 2 warmups + 11 timed rounds per operation
- synthetic valid `impact` JSONL intentionally emitted in non-canonical row/key order
- phase composition is checked against `canonicalize_result_impact`
- idempotence is checked before timing

The clean preserved report is from workflow run `33204960901`; machine-readable data is in `2026-08-28-impact-conformance-v1.json`.

## Clean run

Median latency:

| records | parse + validate | sort | serialize | SHA-256 only | canonicalize result | result digest |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.0066 ms | 0.0004 ms | 0.0043 ms | 0.0008 ms | 0.0121 ms | 0.0189 ms |
| 10 | 0.0545 ms | 0.0015 ms | 0.0334 ms | 0.0013 ms | 0.0940 ms | 0.1011 ms |
| 100 | 0.5420 ms | 0.0170 ms | 0.3237 ms | 0.0056 ms | 0.8960 ms | 0.9137 ms |
| 1,000 | 5.4561 ms | 0.1927 ms | 3.2182 ms | 0.0477 ms | 9.0465 ms | 9.1583 ms |
| 10,000 | 54.2839 ms | 2.6434 ms | 34.6636 ms | 0.4794 ms | 94.0763 ms | 96.8758 ms |
| 50,000 | 274.5657 ms | 16.7840 ms | 179.2325 ms | 2.3668 ms | 475.9352 ms | 481.1938 ms |

At 50,000 records this is approximately:

- `canonicalize_result`: 9.52 µs/record, ~105k records/s;
- `result_digest`: 9.62 µs/record, ~104k records/s;
- parse/validation: 5.49 µs/record;
- serialization: 3.58 µs/record;
- sort: 0.34 µs/record;
- SHA-256: 0.047 µs/record.

Using the independently timed phases, the 50k cost is approximately:

- parse/validation: **58.1%**;
- serialization: **37.9%**;
- canonical sort: **3.5%**;
- SHA-256: **0.5%**.

So the first optimization target, if profiling later justifies optimization at all, is JSON parse/validation and serialization. Hash selection and sort are not meaningful bottlenecks at this scale.

## Runner variance

An immediately preceding equivalent run (`33204844933`) on another GitHub-hosted runner measured 50k records at:

| operation | run 33204844933 | clean run 33204960901 | ratio |
| --- | ---: | ---: | ---: |
| parse + validate | 170.899 ms | 274.566 ms | 1.61× |
| sort | 9.112 ms | 16.784 ms | 1.84× |
| serialize | 104.021 ms | 179.232 ms | 1.72× |
| SHA-256 only | 1.876 ms | 2.367 ms | 1.26× |
| canonicalize result | 291.210 ms | 475.935 ms | 1.63× |
| result digest | 296.235 ms | 481.194 ms | 1.62× |

The decomposition is stable even though absolute wall-clock time is not. This is why the benchmark must not become a hard hosted-runner millisecond threshold.

## Accelerator protocol

When the first physical accelerator exists, measure it against this canonical implementation by executing both in the same benchmark process/run, on the same generated payload, and compare canonical output bytes before comparing time.

The useful performance statistic is then a within-run ratio such as `canonical_ns / candidate_ns`, not an absolute hosted-runner latency. Correctness remains the gate; speedup is measured only after canonical equality and idempotence pass.
