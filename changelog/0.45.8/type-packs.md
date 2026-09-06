---
type: Release Note
title: Optional type packs bootstrap reusable domain vocabularies
---

- Add an opt-in type-pack registry through standard Python package entry points declared in `pyproject.toml`; packs materialize ordinary OKF files and require no special runtime semantics after installation.
- Add the first `journalism` pack, a standards-backed `NewsItem` profile mapped to IPTC ninjs 3.2 with an adjacent DuckDB `.schema.sql` declaration.
- Preserve the important ninjs boundary that `infoSources` describes contributing people/organisations rather than a generic list of documentary evidence, leaving newsroom observation, preservation, and claim-evidence workflow as explicit consumer extensions.
- Make installation preview-first, idempotent, and collision-safe: authored files are never overwritten and any collision aborts the write batch.
