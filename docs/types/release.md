---
type: Spec
title: Release
description: One published version, with what changed in it
---

# Release

A `Release` concept records one published version. Concepts of this type live in
`changelog/`, one document per version, named for the version it describes.

Every pull request adds exactly one, and CI enforces both that and the matching
SemVer bump in `pyproject.toml`.

## Frontmatter

- `type` — always `Release`.
- `title` — `okf-parser <version>`.
- `description` — one sentence on the release, optional on older entries.

## The version is in the filename

`changelog/0.45.2.md` carries its version in its path, not in a frontmatter
field, because the release-contract check derives the expected filename from
`pyproject.toml`. A `version:` field would be a second fact free to disagree with
the first.

The cost is that the version is not queryable as a column. That is the same
trade-off `type_specs.py` documents for derived specification paths, and it is
worth naming rather than discovering.

## This type absorbed ChangelogEntry

Versions 0.1.0 through 0.4.0 were authored as `ChangelogEntry` and everything
from 0.5.0 as `Release`, in the same directory with the same fields. The two
types said the same thing, so the earlier four were migrated to `Release`. There
is one type for this concept, and its name is `Release`.
