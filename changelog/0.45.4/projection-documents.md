---
type: Release Note
title: projection documents, parsed and resolved
---

- Recognize `type: Projection` documents in a bundle (RFC 0018 section 5). A projection names one root concept type and, in `include`, the declared relations to traverse; each `relation` is written `FromType.field` and must resolve to a `FOREIGN KEY` the bundle's `okf.schema.sql` already declares. A projection composes what the relational contract states and cannot invent a relationship.
- Resolve each relation's direction from RFC 0007's rule that N:1 is the primitive: a key *pointing at* the root becomes a list on the root, a key *on* the root becomes a single value. A self-reference is written from the root's own side and therefore reads as single.
- Resolve a composite foreign key from any one of its participating columns, so the case that standalone `--refs=embed` refuses is the case a projection answers — the member's `as` is the name that was missing.
- Refuse, with errors that name the projection: an unknown root, a name colliding with a concept type, a duplicate projection or member name, a relation that is not `FromType.field`, a relation the relational contract does not declare, a relation that connects two types neither of which is the root, and a projection document in a bundle with no relational schema to resolve it against. `optional: true` is the only member-level modifier recognized; any other key is an error rather than a silent no-op, so a mistyped `as` cannot pass for composition.
- Keep projections out of the compiled concept types: a bundle with projection documents no longer exports a `Projection` schema whose fields are `name`, `root` and `include`.

Parsing and resolution only. Compiling a resolved projection into a `TypeContract` and exporting it through `--format` is the next step of the same RFC.
