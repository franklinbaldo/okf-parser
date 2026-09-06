---
type: Spec
title: NewsItem
description: OKF authoring profile for an IPTC ninjs 3.2 news object
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

## Mapping

- `uri` preserves the ninjs globally unique identifier semantics.
- OKF owns the frontmatter key `type`, so the ninjs `type` property is authored
  as `ninjs_type`. Its values retain the ninjs meaning, including `text`,
  `audio`, `video`, `picture`, `graphic`, `composite`, `component`, `event`, and
  `planning`.
- Other ninjs properties keep their standard spelling and semantics whenever
  they are represented in frontmatter.
- The Markdown body is the canonical textual body in the OKF concept. Exporters
  may project it into the appropriate ninjs `bodies` representation instead of
  requiring authors to duplicate the same text in frontmatter.
- Structured properties such as `headlines`, `descriptions`, `subjects`,
  `infoSources`, `places`, and nested event metadata remain ordinary structured
  OKF frontmatter. The starter `.schema.sql` currently captures the scalar
  physical columns that `init --infer-schema` can safely infer; it does not
  redefine or narrow the structured ninjs semantics recorded here.

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

The examples exercise both scalar and structured frontmatter so future changes
to OKF scaffolding are tested against real journalism-shaped data rather than a
pack-specific template language.
