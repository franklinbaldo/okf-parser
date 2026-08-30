---
type: Release Note
title: Define agent-first bundle search
---

- Propose RFC 0016 with one public `search` surface that returns compact, provenance-preserving Markdown evidence through explicitly ephemeral body-line locations such as `path.md#B35-B39`.
- Freeze deterministic offline lexical/literal Phase 1A semantics, exact filter/profile/mode rules, whole-line compact rendering, ranking/tie-breaking, static RFC 0008 MCP effects, and cross-runtime conformance expectations without requiring embeddings, a persistent sidecar, or network access.
- Keep local DuckDB FTS/BM25 as a non-blocking Phase 1B optimization and keep vector, hybrid, ANN, embeddings, and persistent materializations behind later profiles/phases.
- Align retrieval with RFC 0015: structural document IR may select complete Markdown fragments, but raw AST/IR JSON is not default agent context; task success/quality gates benchmarks and actual agent-context tokens remain the primary efficiency metric.
