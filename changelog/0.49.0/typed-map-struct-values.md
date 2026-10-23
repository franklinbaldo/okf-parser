---
type: Release Note
title: Mappings are read into MAP, STRUCT and STRUCT[] columns
---

- A frontmatter mapping, or a list of mappings, under a declared `MAP`, `STRUCT` or `STRUCT[]` column is now read into the typed column instead of becoming `NULL` (#316). The raw column keeps the authored value as JSON text.
- Only authored mappings and lists are read as JSON: a string that merely looks like JSON stays text, and a scalar inside a `STRUCT[]` list is reported as `OKF023`.
- `OKF023` also reports mapping keys a declared `STRUCT` has no field for, which the cast would drop.
