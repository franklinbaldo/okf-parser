---
type: Release Note
title: Optional type packs are authored by dogfooding OKF bundles
---

- add opt-in type-pack discovery through package entry points declared in `pyproject.toml`;
- make pack installation preview-first, idempotent, collision-safe, and free of pack-specific runtime semantics after materialization;
- add the first `journalism` pack with a `NewsItem` profile for IPTC ninjs 3.2;
- build the journalism starter from authored Markdown examples through the existing `okf-parser init --infer-schema` flow instead of embedding spec/schema templates in Python;
- teach starter schema inference to retain consistently structured mapping/list fields as DuckDB `JSON` columns while continuing to omit mixed scalar/structured fields conservatively;
- ship the official ninjs 3.2 and GeoJSON schemas plus the profile mapping with the installed journalism pack;
- map all 41 ninjs root properties, require the generated starter SQL to expose all 41 mapped representations, and fail tests if a root property is missing;
- add a complete root-property fixture that projects back to a ninjs object and validates against the official schema;
- keep `ninjs.type` as the explicit `ninjs_type` rename and make `bodies` fully authorable as structured frontmatter while retaining the Markdown body as a simple-body fallback;
- preserve IPTC `infoSources` semantics rather than treating documentary evidence as information-source parties.
