---
type: Release Note
title: The repository specifies its own concept types
---

- Add a specification document for every concept type in use, at the
  `docs/types/{slug}.md` path `--require-spec` derives, and run the rule
  normatively in CI. The flag shipped in 0.14.0 and the repository that ships it
  had never run it; a new type without a specification now fails the build
  instead of passing unnoticed.
- Migrate `changelog/0.1.0.md` through `0.4.0.md` from `ChangelogEntry` to
  `Release`. Writing both specifications forced the question the split had been
  hiding: same directory, same fields, same meaning, so the earlier four were
  drift left behind by the rename at 0.5.0 rather than a second type.
- Move the 0.45.4 release notes into `changelog/0.45.4/` as a fragment. The
  version arrived while the flat-file format was being retired, and
  `changelog_notes.py` assembles a release only from its fragment directory, so
  the notes would not have reached the published body.
