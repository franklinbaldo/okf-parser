---
type: Benchmark
title: Ordered frontmatter fast path — 2026-08-28
description: Same-host end-to-end comparison of the RFC 0012 Rust engine before and after the canonical simple-frontmatter fast path.
---

# Ordered frontmatter fast path — 2026-08-28

This note records the acceptance benchmark for PR #182. The physical ordering is an optimization opportunity, not semantic validity: every YAML mapping accepted before remains accepted through the existing `yaml-rust2` fallback.

## Physical canonical form

For the deliberately small fast subset, writers prefer:

1. `type`
2. `title`
3. `description`
4. remaining simple top-level keys in lexical order

The direct Rust path accepts only flat mappings with simple scalar spellings in that order. Comments, quoting, nested collections, flow syntax, anchors, tags, multiline values, out-of-order keys, and other YAML features fall back to the full parser.

## Method

Workflow run: `33210930340`.

- GitHub-hosted Ubuntu 24.04 runner.
- Baseline binary built from #181 head `67e72cbe153b57d111649c3e14777ac58f471a96`.
- Candidate binary built from experimental head `d0c95d75571ac7625eb93d6f7d1eeb8164abe01e`.
- Both release binaries were compiled on the same runner.
- Same generated canonical bundle supplied to baseline and candidate.
- A semantically identical shuffled bundle forces the candidate fallback path.
- 1, 10 and 50 thousand documents; one warmup and five timed rounds.
- Before timing, the benchmark requires semantic equality between baseline, candidate fast path, and candidate fallback after excluding only physical `root` and `source_digest` fields where the shuffled source necessarily differs.

## Results

Median end-to-end `__engine-load` latency; lower is better.

| documents | baseline YAML | ordered fast path | candidate fallback | base → fast speedup | latency reduction |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 24.890 ms | 20.324 ms | 26.208 ms | 1.225× | 18.3% |
| 10,000 | 209.325 ms | 165.617 ms | 203.500 ms | 1.264× | 20.9% |
| 50,000 | 1,029.872 ms | 845.361 ms | 1,054.666 ms | 1.218× | 17.9% |

The candidate fallback relative to the old parser was 1.053×, 0.972× and 1.024× at the three sizes. That small mixed variation is consistent with noise plus the cheap failed fast-path probe; there is no evidence here of a material fallback regression.

## Decision

Keep the fast path.

The gain survives the stronger comparison against the pre-change binary on the same host and same canonical files. The effect is consistently about 1.22–1.26× throughput, or roughly 18–21% lower end-to-end load latency on this synthetic simple-frontmatter workload.

This does **not** make field order semantic. A reader must continue to accept noncanonical order and full supported YAML through fallback. The formatter may make the simple physical form canonical because reordering those mapping keys changes `source_digest` but not parsed semantics or `parsed_digest` when the body is unchanged.

Machine-readable measurements are stored in `2026-08-28-ordered-frontmatter-v2.json`.
