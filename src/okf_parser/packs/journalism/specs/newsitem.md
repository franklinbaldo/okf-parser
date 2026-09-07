---
type: Spec
title: NewsItem
description: Complete OKF authoring profile for an IPTC ninjs 3.2 news object
pack: journalism
pack_version: "1"
standard: IPTC ninjs
standard_version: "3.2"
standard_schema: https://www.iptc.org/std/ninjs/ninjs-schema_3.2.json
---

# NewsItem

`NewsItem` is the journalism pack's OKF authoring profile for an IPTC ninjs 3.2 news object. IPTC remains the semantic authority. The pack adapts that model to OKF Markdown instead of defining a parallel journalism ontology.

This spec was bootstrapped by authoring the examples in `../examples/` first and running the normal `okf-parser init --infer-schema` flow over that bundle. The adjacent `.schema.sql` is parser-produced starter physical intent, not a hand-maintained duplicate of ninjs.

The `pack` and `pack_version` metadata record where this starter came from. They are provenance only: after installation the consumer owns ordinary OKF files and may evolve them without retaining a pack-specific runtime dependency.

## Completeness contract

The pack ships the official IPTC ninjs 3.2 JSON Schema and its GeoJSON dependency under `../standards/`. `../ninjs-mapping.json` is the machine-readable bridge between that standard and OKF authorship.

The mapping covers all 41 properties of the official ninjs 3.2 `ninjsType` object. There are two authoring adaptations:

- ninjs `type` is `ninjs_type`, because OKF reserves `type` for concept identity;
- ninjs `bodies` is modeled as an OKF relation to one or more first-class `Body` concepts instead of embedding long content values in `NewsItem` frontmatter.

Every other top-level ninjs property keeps its standard name. Structured properties keep the official nested object/list shape in frontmatter. OKF's parser intentionally preserves scalar spelling, so an exporter restores JSON number, integer, and boolean types from the authoritative ninjs schema rather than inventing local typing rules.

The committed `complete-profile.md` fixture authors every mapped top-level property. Its `bodies` field contains ordinary OKF relation entries whose `resource` values resolve to two `Body` Markdown concepts. Tests require the mapping property set to be exactly equal to the property set in the shipped official schema, resolve the Body relations with the generic OKF relation API, project the result back to ninjs, and validate it against the official schema.

A separate minimal fixture proves the convenience form too: if a `NewsItem` has no explicit `bodies` relation, its own Markdown body may be projected as one simple ninjs body with `contentType: text/markdown`.

## Bodies as concepts

A ninjs body object contains `role`, `contentType`, `charCount`, `wordCount`, and required `value`. In OKF, one such object may be represented by a `Body` Markdown concept:

- `role`, `contentType`, `charCount`, and `wordCount` live in `Body` frontmatter;
- the Markdown body itself is ninjs `value`;
- `NewsItem.bodies` is a list of normal OKF relation mappings such as `resource: content/body-main.md`;
- `resolve_relations(..., field="bodies", target_type="Body")` resolves them without a journalism-specific relation engine.

This makes multiple bodies natural. It also keeps long-form content in Markdown, where it belongs, instead of hiding it inside YAML/JSON-like frontmatter.

The two authoring forms are intentionally exclusive in meaning. If explicit `bodies` relations are present, the linked `Body` concepts are the authoritative body set and the `NewsItem` Markdown body should be empty. If `bodies` is absent, the `NewsItem` Markdown body is the implicit single-body shorthand.

`../body-mapping.json` maps every property of the official ninjs body object. `Body.value` is the only projected property because its value is the Markdown body itself.

## Physical schema

`init --infer-schema` represents consistently structured frontmatter fields as DuckDB `JSON` columns while retaining scalar inference for strings, numbers, dates, and timestamps. Mixed scalar/structured observations are omitted conservatively rather than guessed.

The generated `newsitem.schema.sql` contains one column for every mapped ninjs root property after the OKF naming adaptation. `bodies` remains a `JSON` column because its authored value is a list of OKF relation mappings. The related `Body` type has its own starter table for `role`, `contentType`, `charCount`, and `wordCount`; body text remains Markdown rather than becoming a SQL column.

Arrays and objects such as `headlines`, `infoSources`, `plannedCoverage`, `renditions`, and `associations` are explicit `JSON` columns rather than silently disappearing from the scaffold.

## Mapping

- `uri` preserves the ninjs globally unique identifier semantics.
- `ninjs_type` retains the ninjs `type` meaning, including `text`, `audio`, `video`, `picture`, `graphic`, `composite`, `component`, `event`, and `planning`.
- Other ninjs properties keep their standard spelling and semantics.
- `bodies` resolves to `Body` concepts; each target's Markdown body becomes ninjs `value`.
- When `bodies` is absent, the `NewsItem` Markdown body is the implicit simple body.
- Structured properties remain ordinary structured OKF frontmatter and are validated against their IPTC definitions at the ninjs projection boundary.

## Information sources

`infoSources` keeps its ninjs meaning: parties such as people or organisations that originated, modified, enhanced, distributed, aggregated, supplied, or otherwise contributed information to the news object. It is not a generic list of PDFs, web pages, datasets, decisions, or other documentary evidence.

A newsroom that needs retrieval observations, preservation state, documentary provenance, claim-to-evidence mapping, or workflow state should model those as explicit OKF extensions or related concepts rather than changing the meaning of `infoSources`.

## Standard

The profile targets IPTC ninjs 3.2:

<https://www.iptc.org/std/ninjs/ninjs-schema_3.2.json>

The examples exercise scalar, structured, and relational frontmatter so future changes to OKF scaffolding are tested against real journalism-shaped data rather than a pack-specific template language.
