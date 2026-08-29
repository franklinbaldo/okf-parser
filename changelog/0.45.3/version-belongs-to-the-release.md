---
type: Release Note
title: A version identifies a release, not a pull request
---

- Let several changes declare the same version, because that is the version they will all carry once merged. The CI gate compared each pull request's version against `origin/$BASE` and refused equality, which on a stack means comparing against the previous PR's branch: the gate itself numbered one six-deep chain 0.45.3 through 0.45.8, while two other pull requests still asked for numbers that had already shipped. The gate now enforces the invariant that actually matters -- never reuse a published version, read from the `v*.*.*` tags -- and allows the head version to equal its base.
- Move release notes from one `changelog/X.Y.Z.md` to fragments under `changelog/X.Y.Z/`. Changes that share a version no longer compete for a single file; each contributes its own note and `scripts/changelog_notes.py` assembles them, in sorted file-name order, into the published release body. Prefix a fragment's name if you need it to sort somewhere particular. Releases up to 0.45.2 keep their flat files as an archive.
- Stop publishing frontmatter to GitHub. `finalize` passed `changelog/X.Y.Z.md` straight to `gh release create`, so v0.45.1's release body opens with a byte-order mark and a `---` block; the assembler emits the notes alone.
