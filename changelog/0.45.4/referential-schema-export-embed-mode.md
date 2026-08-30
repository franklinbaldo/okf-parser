---
type: Release Note
title: referential schema export, embed mode
---

- Add `schema --refs=embed`, the second half of RFC 0018's export: a declared reference stops being a scalar and becomes the referenced schema itself. It is emitted **by name**, never re-inlined — `$ref` into the exported `defs` pool for JSON Schema, the sibling `const` for Zod, and the sibling model for Pydantic — so one change to a type reaches every schema that references it in the same regeneration.
- Order Zod declarations by dependency, since a `const` cannot be read before it is declared, and close a cycle with `z.lazy(() => Schema)` on the one edge that ordering cannot satisfy. A self-referencing type is the minimal case and is covered by the corpus.
- Emit Pydantic forward references with postponed annotations and a trailing `model_rebuild()`, and only when an embedded reference actually needs them: a bundle without cycles generates exactly the module it generated before. The generated module is executed in the tests, not merely string-matched, so a cyclic model is proven to import and validate.
- Refuse `--refs=embed` on a composite foreign key with an error that names the constraint and points at projections: embedding replaces the key's columns with one member, and naming that member from N columns would put a naming convention in the parser. `--refs=key` continues to support composite keys.
- Refuse `--refs=embed` without `--relational-schema`, which has nothing to embed.
