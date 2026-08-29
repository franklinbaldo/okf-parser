---
type: Release Note
title: Correct the 0.45.2 release notes
---

- Correct the 0.45.2 changelog, which claimed the restored executable bits reach consumers of the source distribution. Maturin normalizes sdist file modes to `644`; the bits apply to the repository and to clones.
