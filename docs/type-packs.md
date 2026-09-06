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

## Journalism

`journalism` currently provides `NewsItem`, an OKF authoring profile based on IPTC ninjs 3.2. IPTC
remains the semantic authority. The unavoidable field-name adaptation is `ninjs.type` →
`ninjs_type`, because `type` already identifies the OKF concept type.

The examples deliberately include structured ninjs-shaped frontmatter. Today `init --infer-schema`
only proposes scalar physical columns in `.schema.sql`; structured values remain part of the OKF
semantic contract and are not flattened or reinterpreted merely to make starter DDL. This dogfood
case therefore keeps that limitation visible for future schema-scaffolding work.
