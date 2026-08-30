---
type: Release Note
title: projection contracts export across schema formats
---

- Export resolved RFC 0018 `Projection` contracts through JSON Schema, Zod, and Pydantic while preserving every field of the root contract.
- Compose each declared member under its authored `as` name by sibling-schema reference rather than structural re-inline; 1:N members become lists, singular relations remain singular, and composite foreign keys are supported because the projection supplies the composed member name.
- Keep undeclared references on the root in key form while projection-declared members are embedded by name, so the projection remains the sole control over composition.
- Treat `optional: true` as a nullable composed member, matching RFC 0018, and publish JSON Schema `$defs` whenever projection references require them even under the default flat key mode.
