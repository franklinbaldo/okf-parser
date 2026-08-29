---
type: Release Note
title: reduce redundant Rust loader copies
---

- Reduce cold-load allocation in the Rust semantic engine by consuming loaded concept documents by value and borrowing canonical LF-only source through `Cow<str>` instead of always cloning/normalizing full source strings. Preserve byte-for-byte semantic parity and same-host benchmark evidence showing a modest 3–8% end-to-end improvement that grows with bytes per document.
