---
type: Spec
title: Release Note
description: One change's contribution to the notes of the release it lands in
---

# Release Note

A `Release Note` concept is one change's contribution to the notes of the
release it lands in. Concepts of this type are fragments under
`changelog/<version>/`, and `scripts/changelog_notes.py` assembles them in
sorted filename order into the published release body.

## Frontmatter

- `type` — always `Release Note`.
- `title` — what the change did, as a sentence a reader outside the pull request
  can understand.

The assembler strips frontmatter before publishing, so these fields identify the
fragment inside the repository and never reach GitHub.

## Distinguished from Release

They are close enough to be worth separating precisely, because a version and a
note are not the same object:

- A `Release` is one published version. There is at most one per version, and
  older ones survive as flat `changelog/X.Y.Z.md` files.
- A `Release Note` is one change's fragment. A version has as many as it has
  changes, which is the point: several pull requests can land under the same
  version without competing for one file.

`ChangelogEntry` is not a third thing. It was `Release` under an older name and
was migrated.

## Naming a fragment

The filename decides assembly order, so prefix it when a note needs to sort
somewhere particular. Otherwise name it for the change, not for the pull request
that carried it — the number is not durable and the note outlives it.
