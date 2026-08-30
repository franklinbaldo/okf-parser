---
type: Release Note
title: complete native DuckDB read surface
---

- Extend the RFC 0010 DuckDB extension slice with `okf_links(root)`, `okf_reserved(root)` and `okf_diagnostics(root)` alongside `okf_concepts(root)`, preserving unresolved links, reserved documents and document-level diagnostics as typed SQL rows while keeping root/configuration failures as table-function errors.
