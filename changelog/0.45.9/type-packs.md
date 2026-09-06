---
type: Release Note
title: Optional type packs are authored by dogfooding OKF bundles
---

- add opt-in type-pack discovery through package entry points declared in `pyproject.toml`;
- make pack installation preview-first, idempotent, collision-safe, and free of pack-specific runtime semantics after materialization;
- add the first `journalism` pack with a `NewsItem` profile based on IPTC ninjs 3.2;
- build the journalism starter from authored Markdown examples through the existing `okf-parser init --infer-schema` flow instead of embedding spec/schema templates in Python;
- keep the dogfood examples in the package source and test that they reproduce the committed starter `.schema.sql`.
