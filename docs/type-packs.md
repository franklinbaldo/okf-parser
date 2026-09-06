---
type: Documentation
title: Optional type packs
description: Bootstrap reusable OKF vocabularies while keeping the resulting files ordinary and locally editable
---

# Optional type packs

Type packs are opt-in bundles of reusable OKF specifications and declared schemas. They reduce the cost of starting from a consolidated vocabulary without adding a new runtime model to OKF.

The installed files are ordinary OKF files. After materialization, the consumer may inspect, query, validate, version, or adapt them exactly like locally authored specs and schemas.

## Registry

Packs shipped with an installed Python distribution are registered through standard package metadata in `pyproject.toml`:

```toml
[project.entry-points."okf_parser.packs"]
journalism = "okf_parser.type_packs:journalism_pack"
```

The entry-point group is `okf_parser.packs`. Registration is explicit and opt-in at use time; merely installing `okf-parser` does not materialize any pack into a repository.

## CLI

List registered packs:

```console
okf-parser packs
```

Preview installation into the current directory:

```console
okf-parser add-pack journalism .
```

Materialize the previewed pack:

```console
okf-parser add-pack journalism . --write
```

`add-pack` is preview-first. Existing byte-identical files are reported as unchanged. If any destination contains different authored content, the command reports a collision and a write run creates none of the remaining planned files. Packs never overwrite authored files implicitly.

## Journalism pack

The first built-in pack is `journalism`. It is a standards-backed profile based on IPTC ninjs 3.2 rather than a new OKF-specific journalism ontology.

The first vertical slice materializes:

```text
specs/news-item.md
specs/news-item.schema.sql
```

`NewsItem` keeps the semantics of the corresponding ninjs news object. Because OKF already owns the frontmatter key `type`, the ninjs property named `type` is authored as `ninjs_type`; the spec records that mapping explicitly. Structured ninjs collections remain structured metadata and are declared as DuckDB `JSON` columns where appropriate.

The pack records the official ninjs 3.2 JSON Schema URL as provenance but does not copy that schema into the package.

### Information sources are not documentary evidence

The ninjs `infoSources` property describes parties — people or organisations — that originated, modified, enhanced, distributed, aggregated, supplied, or otherwise provided information used by a news object. The journalism pack preserves that meaning.

A newsroom that needs retrieval observations, preserved snapshots, hashes, claim-to-evidence links, or other audit workflow should model those as explicit OKF extensions. It should not overload `infoSources` merely because the local product calls documentary material a "source".

This boundary lets a consumer such as O Vigia adopt consolidated journalism semantics while retaining its own provenance and evidence workflow where the standard intentionally models something else.

## Pack evolution

For now, built-in packs ship with `okf-parser` and their package bytes are versioned by the parser release. A pack also carries its own small pack-version identifier so its authored vocabulary can evolve explicitly.

This initial distribution model is deliberately simple. A future registry or separately versioned package can be introduced only when real consumers show that independent pack release cadence is valuable.
