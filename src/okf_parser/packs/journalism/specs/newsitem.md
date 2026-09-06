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

`NewsItem` is the journalism pack's OKF authoring profile for an IPTC ninjs 3.2
news object. IPTC remains the semantic authority. The pack adapts that model to
OKF Markdown instead of defining a parallel journalism ontology.

This spec was bootstrapped by authoring the examples in `../examples/` first and
running the normal `okf-parser init --infer-schema` flow over that bundle. The
adjacent `.schema.sql` is therefore parser-produced starter physical intent, not
a hand-maintained duplicate of ninjs.

The `pack` and `pack_version` metadata record where this starter came from. They
are provenance only: after installation the consumer owns an ordinary OKF spec
and may evolve it without retaining a runtime dependency on this pack.

## Completeness contract

The pack ships the official IPTC ninjs 3.2 JSON Schema and its GeoJSON schema
dependency under `../standards/`. `../ninjs-mapping.json` is the machine-readable
bridge between that standard and OKF authorship.

The mapping covers all 41 properties of the official ninjs 3.2 `ninjsType`
object. There are two adaptations:

- ninjs `type` is `ninjs_type`, because OKF reserves `type` for concept identity;
- ninjs `bodies` remains available losslessly as structured `bodies` frontmatter,
  with the Markdown body acting as a convenient simple-body fallback when the
  structured field is absent.

Every other top-level ninjs property keeps its standard name. Structured
properties keep the official nested object/list shape in frontmatter. OKF's
parser intentionally preserves scalar spelling, so an exporter restores JSON
number, integer, and boolean types from the authoritative ninjs schema rather
than inventing local typing rules.

The committed `complete-profile.md` fixture authors every mapped top-level
property, including every field of one full `bodies` object. Tests require the
mapping property set to be exactly equal to the property set in the vendored
official schema, require the generated SQL to contain exactly one column for
each mapped root property, project the fixture to ninjs, and validate the result
against the official schema. A separate minimal fixture proves the Markdown-body
fallback also produces valid ninjs.

Nested semantics are not duplicated into a second hand-maintained schema. The
structured frontmatter is shape-preserving, and the shipped IPTC schema remains
the normative definition and validation target for nested fields. This keeps the
profile complete without creating a second source of truth.

## Physical schema

`init --infer-schema` represents consistently structured frontmatter fields as
DuckDB `JSON` columns while retaining scalar inference for strings, numbers,
dates, and timestamps. Mixed scalar/structured observations are still omitted
conservatively rather than guessed.

Consequently the generated `newsitem.schema.sql` contains 41 columns, one for
each ninjs 3.2 root property after the OKF naming adaptation. `ninjs_type`
represents ninjs `type`; `bodies` is an explicit `JSON` column so the full ninjs
body object remains authorable. Arrays and objects such as `headlines`,
`infoSources`, `plannedCoverage`, `renditions`, and `associations` are likewise
explicit `JSON` columns rather than silently disappearing from the scaffold.

## Mapping

- `uri` preserves the ninjs globally unique identifier semantics.
- `ninjs_type` retains the ninjs `type` meaning, including `text`, `audio`,
  `video`, `picture`, `graphic`, `composite`, `component`, `event`, and
  `planning`.
- Other ninjs properties keep their standard spelling and semantics.
- `bodies` preserves the complete ninjs array. When it is absent, the Markdown
  body is projected to a simple body with `contentType: text/markdown`.
- Structured properties remain ordinary structured OKF frontmatter and are
  validated against their IPTC definitions at the ninjs projection boundary.

## Information sources

`infoSources` keeps its ninjs meaning: parties such as people or organisations
that originated, modified, enhanced, distributed, aggregated, supplied, or
otherwise contributed information to the news object. It is not a generic list
of PDFs, web pages, datasets, decisions, or other documentary evidence.

A newsroom that needs retrieval observations, preservation state, documentary
provenance, claim-to-evidence mapping, or workflow state should model those as
explicit OKF extensions or related concepts rather than changing the meaning of
`infoSources`.

## Standard

The profile targets IPTC ninjs 3.2:

<https://www.iptc.org/std/ninjs/ninjs-schema_3.2.json>

The examples exercise scalar and structured frontmatter so future changes to OKF
scaffolding are tested against real journalism-shaped data rather than a
pack-specific template language.
