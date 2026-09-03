---
type: Release Note
title: Add offline bundle search core
---

- Add the RFC 0016 Phase 1A search core over already-loaded bundles.
- Ship deterministic `builtin_lexical_v1` BM25-style ranking and Unicode-case-folded literal matching with no network or index dependency.
- Preserve exact body-line provenance, positive path/type filters, context expansion, compact/score/full rendering, and canonical location escaping.
- Add language-neutral search conformance fixtures for future TypeScript parity.
