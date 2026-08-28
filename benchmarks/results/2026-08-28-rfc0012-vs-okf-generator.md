# RFC 0012 substrate vs `okf-generator` v0.1.53

Date: 2026-08-28

This note records a same-host benchmark used while reviewing RFC 0012. It is on
a dedicated benchmark branch and is not part of PR #178.

## Versions and host

- `okf-parser`: RFC 0012 product tree at `0c50bdb86d4ae6564776973a1b459aec8d387449`; benchmark branch adds only this harness/results material.
- `okf-generator`: `5fb73be73814ec75b5c5f48ccbbecf348ff277d3` (`0.1.53`, current upstream `main` at measurement time).
- GitHub-hosted Ubuntu 24.04 runners, CPython 3.12.14.
- Synthetic authored bundle sizes: 1,000, 10,000 and 50,000 concepts.
- Each concept has YAML frontmatter, one Markdown relation to the next concept,
  and alternates between `BenchmarkA` and `BenchmarkB`.
- Setup/install time is excluded.

## Bundle ingest / current derived-store build

Milliseconds; lower is better.

| concepts | generator raw Markdown load | parser Python/Ibis load | parser Rust engine load | generator SQLite build | parser current `attach_okf` DuckDB build |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 403.35 | 2,174.28 | 21.06 | 389.01 | 686.95 |
| 10,000 | 4,067.66 | 6,580.80 | 176.41 | 3,828.12 | 6,592.78 |
| 50,000 | 20,313.12 | 33,384.03 | 899.11 | 18,955.11 | 33,244.54 |

Interpretation:

- The direct Rust semantic-engine path is roughly 19.1x, 23.1x and 22.6x
  faster than `okf-generator`'s uncached Markdown bundle loader at 1k, 10k and
  50k respectively. The work is not perfectly identical: `okf-parser` also
  enforces its canonical parse/validation/link/digest semantics.
- The current Python/Ibis load path is slower than the generator raw loader
  (about 5.4x at 1k and 1.6x at 10k/50k).
- The current `attach_okf` path is about 1.7–1.8x slower to build its persistent
  DuckDB snapshot than the generator is to build its SQLite store. This is the
  current Python/Ibis materialization path, not RFC 0010 native table functions
  or RFC 0012 milestone 1b. Do not project this build time onto the future native
  provider.

At 50k, direct Rust engine throughput is about 55.6k concepts/s; the generator
uncached Markdown loader is about 2.46k concepts/s on this corpus.

## Product-level hot operations

Microseconds per call, average of 200 warm calls. These calls intentionally use
each project's existing public/current surface, so payload work is not identical:
`okf-generator` hydrates complete concept dictionaries/tags while the DuckDB
queries below select only the fields needed for the benchmark.

| concepts | generator point lookup | DuckDB point lookup | generator type filter (80) | DuckDB type filter (80) | generator neighbors | DuckDB edge neighbors |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 18.50 | 361.30 | 1,007.44 | 505.15 | 48.53 | 399.90 |
| 10,000 | 19.01 | 407.39 | 2,134.53 | 511.19 | 49.96 | 519.99 |
| 50,000 | 19.30 | 632.37 | 8,546.45 | 511.96 | 51.12 | 1,059.35 |

The relative factors should not be treated as engine-vs-engine results because
the returned payloads differ. They do show the product trade-off: the generator
has dedicated SQLite indexes for singleton/adjacency access; the current DuckDB
snapshot remains sub-millisecond to about one millisecond for these operations
but scans unindexed relational tables.

## Equivalent SQLite vs DuckDB queries

To isolate the derived stores, a second run used exactly the same selected
columns and predicates in raw SQL, averaged over 500 warm calls.

Microseconds; lower is better.

| concepts | operation | generator SQLite | parser DuckDB |
| ---: | --- | ---: | ---: |
| 10,000 | point lookup by concept id | 7.52 | 461.43 |
| 10,000 | type filter + order + limit 80 | 1,504.89 | 1,030.57 |
| 10,000 | count by type | 209.06 | 598.31 |
| 10,000 | incident edges | 9.40 | 606.16 |
| 50,000 | point lookup by concept id | 7.75 | 739.00 |
| 50,000 | type filter + order + limit 80 | 9,051.06 | 2,549.18 |
| 50,000 | count by type | 1,037.86 | 1,151.40 |
| 50,000 | incident edges | 9.53 | 1,242.62 |

At 50k this means:

- SQLite point lookup is about 95x faster (7.75 us vs 739 us).
- SQLite incident-edge lookup is about 130x faster (9.53 us vs 1.24 ms).
- DuckDB ordered type-filter + limit is about 3.55x faster (2.55 ms vs 9.05 ms).
- Full type count is near parity: SQLite about 1.04 ms, DuckDB about 1.15 ms.

The relative point/edge factors are large but the DuckDB absolute latency is
still below ~1.3 ms at 50k in this synthetic corpus. The result is a reason to
profile/index keyed navigation when implementing RFC 0012, not a reason to move
canonical relational semantics into SQLite.

## Important non-comparisons

`okf-generator`'s published `okf generate` benchmark (source-code discovery,
tree-sitter AST parse, linking, and writing tens of thousands of generated
Markdown files) is not the same workload as `okf-parser` loading an already
authored OKF bundle. Likewise, the parser's direct Rust engine timings are not a
benchmark of code-to-OKF generation. Cross-project headline timings must not be
divided into a claimed speedup unless the work is equivalent.

Disk-size readings were deliberately omitted from the conclusions because both
SQLite and DuckDB use WAL/checkpoint behavior that made naive main-file size
sampling misleading while connections were open.
