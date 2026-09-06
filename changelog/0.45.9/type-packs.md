---
type: Release Note
title: Optional type packs are authored by dogfooding OKF bundles
---

- add opt-in type-pack discovery through package entry points declared in `pyproject.toml`;
- make pack installation preview-first, idempotent, collision-safe, and free of pack-specific runtime semantics after materialization;
- add the first `journalism` pack with `NewsItem` and first-class Markdown `Body` types for IPTC ninjs 3.2;
- build journalism starter schemas from authored Markdown examples through the existing `okf-parser init --infer-schema` flow instead of embedding schema templates in Python;
- teach starter schema inference to retain consistently structured mapping/list fields as DuckDB `JSON` columns while continuing to omit mixed scalar/structured fields conservatively;
- ship the official ninjs 3.2 and GeoJSON schemas plus machine-readable NewsItem and Body mappings with the installed journalism pack;
- map all 41 ninjs root properties and all 5 properties of a ninjs body object, failing conformance tests if either standard shape drifts without a profile update;
- model `NewsItem.bodies` as ordinary OKF relations to one or more `Body` concepts, using each Body file's Markdown content as ninjs `value` and its frontmatter for role/content type/count metadata;
- retain the `NewsItem` Markdown body as an implicit single-body shorthand only when no explicit `bodies` relations are authored;
- resolve multi-body fixtures through the generic OKF `resolve_relations` API and validate the projected result against the official ninjs Draft 2020-12 schema;
- keep `ninjs.type` as the explicit `ninjs_type` rename and preserve IPTC `infoSources` semantics rather than treating documentary evidence as information-source parties.
