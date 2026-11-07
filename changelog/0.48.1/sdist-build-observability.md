---
type: Release Note
title: Cold sdist build timing evidence
---

- Record wall-clock and Cargo timing evidence for the mandatory cold source-distribution consumer without restoring caches, native wheels, or prebuilt DuckDB.
- Upload the cold sdist timing JSON and Cargo HTML so the remaining source-build bottleneck can be optimized from measured crate-level costs.
