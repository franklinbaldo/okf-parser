---
type: Release Note
title: Sparse authoring and declared defaults
---

# Sparse authoring and declared defaults

OKF concepts no longer gain conventional `title` or `description` fields in
generated contracts unless those fields were observed or declared. `init`
scaffolds only the minimal type specification instead of placeholder prose.

Declared DuckDB `DEFAULT` expressions are now retained from `.schema.sql`
and applied in typed materialization when an authored field is absent or YAML
null. A present malformed value still becomes typed `NULL`; it never silently
falls back to the default. See RFC 0025.
