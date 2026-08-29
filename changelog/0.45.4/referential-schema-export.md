---
type: Release Note
title: referential schema export proposal
---

- Add RFC 0011, proposing referential schema export and declared projections: `schema` learns to read the bundle's `okf.schema.sql` so a field participating in a declared foreign key compiles to a reference instead of a closed scalar, and an optional `type: Projection` document composes a root type with declared relations. Both are opt-in; without the new flag and without projection documents the export is unchanged. The RFC also proposes emitting `export type X = z.infer<typeof XSchema>` from the Zod renderer, which removes the hand-written barrel every TypeScript consumer maintains today.
