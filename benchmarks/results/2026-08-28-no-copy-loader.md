---
type: Benchmark
title: No-copy Rust loader benchmark
---

# No-copy Rust loader benchmark

Same-host end-to-end measurement of the Rust bundle loader after removing redundant whole-source copies for concept documents and avoiding newline-normalization allocation for canonical LF sources.

## Contract

The benchmark compares the exact #182 baseline commit `949331cd4f0b8b20b302b5ff6b09e72d3564bf3b` with candidate `0ef344c53518441e3d71dfe7d2622a2871950c09` on the same GitHub-hosted Ubuntu 24.04 runner.

Before timing any case, both release binaries load the same authored bundle through `__engine-load` and their stdout must be byte-for-byte identical. The benchmark aborts on any semantic or serialization difference.

Run: `33217626605`. Five measured rounds after one warmup per binary and case, with read concurrency 32.

## Results

| Documents | Body/document | Baseline | Candidate | Speedup | Latency reduction |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 50,000 | 64 B | 768.006 ms | 741.290 ms | 1.036x | 3.48% |
| 10,000 | 1 KiB | 228.534 ms | 214.632 ms | 1.065x | 6.08% |
| 1,000 | 16 KiB | 122.625 ms | 112.876 ms | 1.086x | 7.95% |

The benefit increases with bytes per document, which is consistent with the intended mechanism: fewer full-document copies and no newline-normalization allocation for LF-only authored OKF.

## Interpretation

This is a modest cold-load optimization rather than an order-of-magnitude accelerator. It is nevertheless end-to-end, monotonic with document size in this matrix, semantics-preserving, and essentially complexity-neutral at the public API boundary.

The implementation keeps `source_digest` over the original source text. `parsed_digest` continues to use normalized semantic content. Documents containing carriage returns still allocate the normalized representation and preserve the previous behavior.

The larger remaining opportunity is incremental snapshot reuse: unchanged documents should eventually avoid read/parse/hash work entirely rather than merely making a cold load cheaper.
