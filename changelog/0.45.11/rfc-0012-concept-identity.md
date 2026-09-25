---
type: Release Note
title: Concept identity and rename semantics documented
---

# Concept identity and rename semantics documented

RFC 0012 was cited by number and quoted verbatim by RFCs 0014, 0016 and
0017 but never existed in the repository. It is now written down: how
`concept_id`/`logical_key` are derived from a concept's path, why a rename
is a removal plus an addition rather than a tracked move, and why digest
equality is advisory evidence a caller may compute rather than an
inference the parser makes on its own.

`docs/architecture.md` gains a short "Concept identity" summary, and
`tests/test_concept_identity.py` pins the rename/move behavior with
regression tests.
