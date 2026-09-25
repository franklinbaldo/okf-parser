---
type: Release Note
title: Concept identity and rename semantics documented
---

# Concept identity and rename semantics documented

RFC 0023 extracts the identity section of the still-proposed RFC 0012, which
RFC 0017 already relies on, into a standalone decision. It writes down how
`concept_id`/`logical_key` are derived from a concept's path, why a rename
is a removal plus an addition rather than a tracked move, and why digest
equality is advisory evidence a caller may compute rather than an
inference the parser makes on its own.

`docs/architecture.md` gains a short "Concept identity" summary, and
`tests/test_concept_identity.py` pins the rename/move behavior with
regression tests.
