---
type: Release Note
title: reference depth and composite-key rules
---

- Amend RFC 0018 with the two controls its first draft left open. `--refs=embed` stays a per-run binary mode rather than a per-field selector: a projection is where per-member composition is declared. Depth moves to projections, where it is answerable — a flat export emits one schema per concept type and every type is a root there, so bounding hops would need two variants of the same contract; a projection has exactly one root, and a member beyond its bound degrades to the reference's key form instead of being dropped.
- Settle composite foreign keys, which `ForeignKeyConstraint` has described since RFC 0007 and which the motivating bundle needs on its first reference: `--refs=key` supports them, projections embed them under the member's `as` name, and standalone `--refs=embed` rejects them with a normative error that names the constraint and points at projections. Naming a composed member from N columns would put a naming convention in the parser, which the RFC's first decision forbids.
