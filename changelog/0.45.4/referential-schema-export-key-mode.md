---
type: Release Note
title: referential schema export, key mode
---

- Implement the first half of RFC 0018: `schema --relational-schema PATH` reads the bundle's `okf.schema.sql` and compiles every field participating in a declared `FOREIGN KEY` to a reference node instead of to the bare scalar it carries. Composition is read from the RFC 0007 contract that already exists — no second place to declare that two types are related, and no naming or folder convention consulted.
- Publish the reference in each format's own idiom: JSON Schema gains `x-okf-references` (target type, the constraint's columns in authored order, the referenced columns, and this field's position in the key), Zod gains a `.describe("references Type(column)")` that leaves `z.infer` untouched, and Pydantic carries the same payload in `Field(json_schema_extra=...)`. The field keeps the scalar type it always had: under key mode a reference is a type-level fact about the value, not a promise that the consumer holds the referenced document.
- Support composite foreign keys, which `ForeignKeyConstraint` has described since RFC 0007: every participating column carries the whole constraint and its own position, so a consumer can reassemble the key without guessing its order.
- Keep the whole feature opt-in and default-off. Without `--relational-schema` the exported JSON Schema, Zod and Pydantic are byte-identical to before, which the regression corpus asserts directly. A relative path resolves against the bundle root, matching `check --relational-schema`.
- Report, rather than silently skip, a declared foreign-key column that no document of its type carries, and a column claimed by two foreign keys.
