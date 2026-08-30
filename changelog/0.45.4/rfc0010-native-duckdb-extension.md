---
type: Release Note
title: native DuckDB extension proposal
---

- Propose RFC 0010 for a loadable DuckDB extension backed by the shared `okf-engine`: keep OKF semantics DuckDB-independent, expose concepts/links/reserved/diagnostics as typed table functions, retain Python/TypeScript/CLI fallbacks, and treat projection pushdown, bounded chunking, cancellation and Community Extension distribution as explicit implementation gates.
