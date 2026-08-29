---
type: Release Note
title: Draft RFC 0017, the provider-local fact profile
---

- Draft RFC 0017, the `fact` profile, scoped explicitly to native `.fact/`
  contexts so plain OKF keeps the identity, reserved filenames and vocabulary
  the base format and RFC 0012 already pin. A markerless directory is read as a
  compatibility view, and retiring `index.md`/`log.md` or renaming
  `bundle`/`concept` stays profile-local until a migration of its own.
- Reconcile the profile's stable fact identity with RFC 0012 by defining
  `.fact/` as a distinct provider rather than a reinterpretation of the
  filesystem provider. Identity still comes from the provider and never from
  content: the id is authored, not inferred from equal digests.
- Add `examples/minimal`, the smallest bundle `check` accepts as conformant,
  validated by the repository's own `check` run.
