---
type: BenchmarkResult
title: Agent token cost — 1k deterministic trace
description: Reproducible 1,000-document context trace comparing full Markdown, generic retrieval, and okf-parser
date: 2026-08-29
---

# Agent token cost — 1k deterministic trace

## Result

This run measures the primary deterministic precursor to the live agent benchmark: the number of input/context tokens presented once the required evidence has been selected. It does **not** include model-driven retrieval planning; the live agentic benchmark covers that separately.

Across the six tasks, the strategy-level p50 was **123,096 input tokens** for full authored Markdown, **130** for generic Markdown retrieval, and **137** for `okf-parser` structured retrieval.

Both retrieval strategies reduce context by about **99.9%** relative to full-corpus Markdown. In this fixture, however, `okf-parser` is **7 tokens (5.4%) more expensive** than generic retrieval at aggregate p50. This run therefore demonstrates a retrieval advantage, not an OKF-specific token advantage.

## Protocol

- GitHub Actions source run: `33265896256` (`agent-workload-smoke`)
- model/tokenizer target: `gpt-5.6-sol` / `o200k_base`
- token source: `local tokenizer`
- rounds: 2
- corpus: 1,000 documents, ~512 body bytes each
- authored Markdown storage: 563,890 bytes
- full authored Markdown: 123,000 tokens
- full OKF canonical projection: 196,010 tokens

## Per-task context

| task | direct Markdown | generic retrieval | okf-parser | OKF Δ vs generic | OKF vs generic |
| --- | ---: | ---: | ---: | ---: | ---: |
| `lookup_factual` | 123,098 | 131 | 138 | +7 | +5.3% |
| `find_concept_type` | 123,081 | 96 | 103 | +7 | +7.3% |
| `cross_reference` | 123,100 | 192 | 199 | +7 | +3.6% |
| `relation_navigation` | 123,088 | 129 | 136 | +7 | +5.4% |
| `synthesis` | 123,095 | 126 | 133 | +7 | +5.6% |
| `irrelevant_corpus` | 123,099 | 132 | 139 | +7 | +5.3% |

All three deterministic evidence paths satisfied the same exact JSON oracle for every run. `tokens_per_success` therefore equals mean input tokens here; this is a quality/evidence gate, not an LLM-answer-quality claim.

## What this establishes

- Sending the complete corpus is an extremely expensive baseline for sparse tasks at this scale.
- Selective retrieval is the dominant source of the observed context reduction.
- The canonical OKF projection is larger than authored Markdown when serialized in full, yet task-level OKF retrieval still uses only a tiny fraction of the corpus. Storage size and agent-consumed context are therefore genuinely different metrics.
- The small OKF overhead in this deterministic fixture comes from the representation/instruction surface. It must not be described as a token win over generic retrieval.

## What remains for the live benchmark

The decisive follow-up is model-selected retrieval: give the same model either generic Markdown tools or OKF-native tools, let it decide which calls to make, and sum official provider `input_tokens` across **every** call. Tool definitions, tool results, follow-up calls, output tokens, latency, correctness, and `tokens_per_success` are recorded. That live run determines whether OKF structure reduces navigation/planning cost enough to offset its small deterministic representation overhead.
