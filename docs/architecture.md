---
type: Architecture
title: Strict OKF core and source-adapter boundary
---

# Architecture boundary

`okf-parser` keeps one strict core and puts source adaptation outside that core.

```text
discovery / classification
        ↓
strict authored OKF parse
        ↓
authoritative normalized relations
        ↓
TypeContract
        ↓
typed DuckDB / Ibis relations
        ↓
consumer projections and adapters
```

The core answers what an authored OKF bundle says and whether it conforms. A non-reserved Markdown concept therefore needs authored OKF frontmatter and a non-empty `type`. Core graph relations come from authored Markdown links; producer-defined frontmatter strings remain data, even when they look like paths. Utility classifiers may still recognize path- or link-shaped strings for callers, but classification alone does not grant normative relation semantics.

OKF graphs mix more than one kind of edge, and they must not be conflated: a Markdown navigation link (**structural**), a producer-declared relation over typed data (**semantic**, e.g. RFC 0007/0021), a claim that one fact supports or contradicts another (**epistemic**, not yet implemented as a core mechanism), and a record of where a fact or edge came from (**provenance**, e.g. RFC 0009's Git-derived facts). RFC 0022 names these categories and requires every graph export to carry one, so a consumer never has to infer semantics from a plain link.

External corpora use a separate adaptation boundary:

```text
external source (MDX, legacy Markdown, Agent Skills, ...)
        ↓
source adapter / projection policy
        ↓
projection plan + explicit provenance
        ↓
canonical OKF representation
        ↓
ordinary core pipeline
```

An adapter may derive an effective type, rewrite a source relation into the projected namespace, or recognize a source dialect. It must preserve enough provenance to distinguish authored evidence from projection policy. The downstream graph, schema, DuckDB/Ibis and MCP surfaces should consume the canonical OKF representation instead of learning every source dialect independently.

`tests/test_parser_validator_boundary.py` pins the strict-core boundary down as regressions: `parse_document` accepts a missing or unrecognized `type` and preserves unknown frontmatter fields, while `load_bundle` is the layer that reports `OKF002` and every other normative rule by its stable code.

This is primarily an internal architecture rule. Ordinary conformant bundles should not need adapter configuration, profiles or extra command hierarchy. Advanced adaptation is progressive disclosure.

## Concept identity

A concept's `concept_id` and `logical_key` are derived from its bundle-relative path at load time, not authored and not stored independently of that path. Moving or renaming a concept therefore produces an unrelated `concept_id` at the new path; the parser represents this as one concept disappearing and another appearing, never as a tracked move. `source_digest`/`parsed_digest` equality between such a pair is evidence a caller may compute to suspect continuity, but it is advisory only — the parser never infers or merges identity from content. A stale frontmatter reference to a pre-move path fails loudly (`KeyError`) rather than being silently repaired. See [RFC 0012](../rfcs/0012-concept-identity-and-rename-semantics.md) for the full contract and rationale, and [RFC 0017](../rfcs/0017-fact-profile.md) for how a profile-scoped provider may add move-stable identity on top of this.
