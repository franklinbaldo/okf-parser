---
type: Release Note
title: Python objects can opt into canonical OKF serialization
---

- Add `OKFDocument`, the structural `SupportsOKF` protocol, `to_okf()` normalization, and `dumps()` so domain objects can explicitly project themselves into deterministic OKF without inheritance or reflection.
- Keep YAML syntax and canonical physical ordering inside `okf-parser`, reject unsupported Python values before emission, and preserve the existing style-aware edit path for authored documents.
