---
type: Release Note
title: reference depth and composite-key rules
---

- Amend RFC 0018 with the two controls its first draft left open. `--refs=embed` stays a per-run mode — a projection is where per-member composition is declared — and gains `--depth=N`, which bounds how many reference hops embedding follows and degrades the rest to their key form. `--depth=1` is the default, so a cyclic domain graph no longer renders nearly every node through `z.lazy`; unbounded embedding must be asked for with `--depth=max`.
- Settle composite foreign keys, which `ForeignKeyConstraint` has described since RFC 0007 and which the motivating bundle needs on its first reference: `--refs=key` supports them, projections embed them under the member's `as` name, and standalone `--refs=embed` rejects them with a normative error that names the constraint and points at projections. Naming a composed member from N columns would put a naming convention in the parser, which the RFC's first decision forbids.
