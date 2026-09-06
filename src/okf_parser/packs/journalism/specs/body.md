---
type: Spec
title: Body
description: First-class OKF Markdown body projected to one IPTC ninjs 3.2 bodies item
pack: journalism
pack_version: "1"
standard: IPTC ninjs
standard_version: "3.2"
standard_schema: https://www.iptc.org/std/ninjs/ninjs-schema_3.2.json
---

# Body

`Body` represents one item of the IPTC ninjs 3.2 `bodies` array as an ordinary OKF Markdown concept.
The Markdown body itself is the authored content and projects to ninjs `value`.

The remaining ninjs body properties are frontmatter metadata:

- `role`: optional role of this body;
- `contentType`: optional IANA media type, normally `text/markdown` for an authored Markdown body;
- `charCount`: optional character count;
- `wordCount`: optional word count.

`value` is deliberately not duplicated in frontmatter. It is the Markdown body.

A `NewsItem` may link one or more `Body` concepts through its `bodies` relation. This uses the normal OKF relation shape (`resource`) and the generic `resolve_relations(..., field="bodies", target_type="Body")` API; the journalism pack does not introduce a second relation engine.

For a simple `NewsItem`, an absent `bodies` relation may still use the `NewsItem`'s own Markdown body as an implicit single body. Once explicit `bodies` relations are present, those linked `Body` concepts are the authoritative body set and the `NewsItem` body should be empty to avoid two competing content sources.

The machine-readable `../body-mapping.json` maps all five properties of a ninjs body object: four direct metadata fields plus Markdown body → `value`.
