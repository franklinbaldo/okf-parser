---
type: Release Note
title: canonical Rust Markdown AST proposal
---

- Documentation/RFC only; no runtime behavior changes.
- Propose RFC 0015: make the Rust engine the authoritative Markdown parser while keeping raw `mdast` private behind an OKF-owned, versioned document protocol.
- Define frozen/closed Pydantic and TypeScript boundary models, source spans over a normalized parser snapshot, exact-source guarding through `source_digest`, and an internal `SourceMap` for lossless BOM/newline-aware edits.
- Keep generated RFC 0001 Pydantic models focused on producer-authored frontmatter, keep `Bundle` and relational records compact, and make full AST serialization opt-in.
- Add convergence gates with #182/#183 performance fast paths, #189 structural retrieval, and #104 Git/provenance ownership.
