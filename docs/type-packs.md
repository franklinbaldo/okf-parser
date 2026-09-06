---
type: Documentation
title: Optional type packs
description: Bootstrap reusable domain types without adding runtime semantics
---

# Optional type packs

Type packs are opt-in starters made from ordinary OKF specs and schemas. Installing a pack copies
those files into the consumer bundle. From that point onward they are normal authored OKF files:
the project may edit or fork them and does not depend on a pack-specific runtime.

Available packs are registered through Python package metadata in `pyproject.toml` under the
`okf_parser.packs` entry-point group. Pack contents stay inside the `okf-parser` distribution for
now.

## Commands

List installed packs:

```console
okf-parser packs
```

Preview what a pack would add to the current bundle:

```console
okf-parser add-pack journalism .
```

Materialize it explicitly:

```console
okf-parser add-pack journalism . --write
```

Installation never overwrites different authored files. Existing byte-identical files are reported
as unchanged; any collision aborts the whole write batch.

## Dogfood contract

A pack is developed from concepts first, not from strings embedded in Python. The journalism pack
is the reference workflow:

```text
examples/*.md
    ↓
okf-parser init PACK --spec-template 'specs/{slug}.md' --infer-schema --write
    ↓
specs/<type>.md + specs/<type>.schema.sql
    ↓
enrich the generated spec with the domain standard's semantics
    ↓
package and install those ordinary OKF files
```

Tests rerun `init --infer-schema` against the authored examples and require the committed starter
schemas to match. This makes packs pressure the same public path used by ordinary consumers instead
of acquiring a second template engine.

Structured fields participate in that dogfood path too. If every non-null observation of a field
is a mapping or list, starter DDL represents the field as DuckDB `JSON`. Scalar fields retain the
existing conservative type inference. A field observed both as structured and scalar is omitted
rather than guessed.

## Journalism

`journalism` provides two cooperating OKF types: `NewsItem`, the profile for the root IPTC ninjs
3.2 news object, and `Body`, the Markdown representation of one item in ninjs `bodies`. IPTC
remains the semantic authority. The installed pack includes both generated specs/schemas, the
machine-readable mappings, the official ninjs 3.2 JSON Schema, and its GeoJSON dependency.

The root mapping is checked against the official schema. All 41 top-level ninjs 3.2 properties must
be represented. `ninjs.type` becomes `ninjs_type` because OKF owns `type`. Every other root property
preserves its ninjs name, while `bodies` changes only its authoring representation: it is an OKF
relation list pointing at `Body` concepts.

A `Body` is an ordinary Markdown file. Its frontmatter carries the four optional ninjs body metadata
properties (`role`, `contentType`, `charCount`, `wordCount`), and the Markdown body itself projects
to the required ninjs `value`. `body-mapping.json` is checked against all five properties of the
official ninjs body object.

This makes multi-body content natural:

```yaml
bodies:
  - resource: content/main.md
  - resource: content/background.md
```

The links use the existing generic OKF relation contract and can be resolved with
`resolve_relations(..., field="bodies", target_type="Body")`. No journalism-specific graph or
relation engine is added.

For the common single-body case, `bodies` may be omitted and the `NewsItem`'s own Markdown body acts
as an implicit body with `contentType: text/markdown`. If explicit `bodies` relations are present,
the linked `Body` concepts are authoritative and the `NewsItem` body should be empty, avoiding two
competing content sources.

Structured values such as `plannedCoverage`, `renditions`, `associations`, `infoSources`, people,
organisations, places, events, subjects, rights and trust indicators keep their nested ninjs shape
in frontmatter. Their starter SQL columns are `JSON`; the complete nested semantic definition is
not duplicated into SQL because the shipped IPTC schema is authoritative.

The conformance fixture authors all 41 root properties, links two separate Body Markdown fixtures,
resolves those links with OKF's generic relation API, projects the graph back to a ninjs object, and
validates it against the official Draft 2020-12 schema. A separate fixture validates the implicit
single-body Markdown shorthand. Schema drift cannot silently leave either a root property or a body
property unrepresented.

This distinction also matters for `infoSources`: it retains the IPTC meaning of information-supplying
people or organisations. Documentary evidence, retrieval observations, preservation state and
claim-to-evidence relations remain explicit newsroom/OKF concepts instead of being smuggled into a
standard field with different semantics.
