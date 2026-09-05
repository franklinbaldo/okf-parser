---
type: Release Note
title: Declared schemas export before the first concept instance
---

- `schema` exports a type whose `ConceptSpecification` already has a sibling `.schema.sql` even when the bundle has not produced its first concrete document of that type yet.
- Discovery reads the authored `concept_type` from the specification, preserving type identity instead of reverse-engineering it from filesystem slugs.
- JSON Schema, Zod and Pydantic therefore support contract-first workflows such as agent run scaffolds and future event types.
