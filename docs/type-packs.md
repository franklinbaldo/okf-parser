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
schema to match. This makes packs pressure the same public path used by ordinary consumers instead
of acquiring a second template engine.

Structured fields participate in that dogfood path too. If every non-null observation of a field
is a mapping or list, starter DDL represents the field as DuckDB `JSON`. Scalar fields retain the
existing conservative type inference. A field observed both as structured and scalar is omitted
rather than guessed.

## Journalism

`journalism` provides `NewsItem`, a complete OKF authoring profile for the root IPTC ninjs 3.2 news
object. IPTC remains the semantic authority; the pack vendors the official ninjs 3.2 JSON Schema
and its GeoJSON dependency as fixed conformance references.

A machine-readable mapping is checked against the official schema. All 41 top-level ninjs 3.2
properties must be classified. There are only two adaptations: `ninjs.type` becomes `ninjs_type`
because OKF owns `type`, and ninjs `bodies` is projected from the Markdown body. Every other root
property preserves the ninjs name.

Structured values such as `plannedCoverage`, `renditions`, `associations`, `infoSources`, people,
organisations, places, events, subjects, rights and trust indicators keep their nested ninjs shape
in frontmatter. Their starter SQL columns are `JSON`; the complete nested semantic definition is
not duplicated into SQL because the vendored IPTC schema is the authoritative definition.

The conformance fixture authors every non-projected root property. Tests then project that OKF
fixture back to a ninjs object, restore JSON scalar types according to the official schema, and
validate the result with the official Draft 2020-12 schema. The tests also require the mapping's
property set to equal the official `ninjsType` property set exactly, so schema drift cannot silently
leave a ninjs root property unrepresented.

This distinction matters for `infoSources`: it retains the IPTC meaning of information-supplying
people or organisations. Documentary evidence, retrieval observations, preservation state and
claim-to-evidence relations remain explicit newsroom/OKF concepts instead of being smuggled into a
standard field with different semantics.
