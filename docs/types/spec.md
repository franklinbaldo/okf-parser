---
type: Spec
title: Spec
description: A document that specifies what one concept type means and which frontmatter it carries
---

# Spec

A `Spec` document states what one concept type means, where its concepts live,
and which frontmatter fields they carry. `check --require-spec` derives the
expected path for every type in use from the template it is given; this
repository uses `docs/types/{slug}.md`.

The path is derived, never declared. `type_specs.py` explains why: a `spec:`
frontmatter field would be a second fact free to disagree with the first, and
putting the path in `type` itself would tie identity to layout.

## Frontmatter

- `type` — always `Spec`.
- `title` — the type this document specifies.
- `description` — one sentence on what the type is for.

## What a Spec is not

A `Spec` does not validate anything by existing. It records intent so that a
reader can tell whether a concept is using its type correctly, and so that drift
between two types that describe the same thing becomes visible.

Physical column types are a separate, optional declaration: RFC 0006 reads them
from a `.schema.sql` beside this document. No type in this repository declares
one yet, because no type here carries structured frontmatter worth typing.
