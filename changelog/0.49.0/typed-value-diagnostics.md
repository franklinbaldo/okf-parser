---
type: Release Note
title: Declared values a typed column cannot hold are reported (OKF023)
---

- A frontmatter value that its declared `.schema.sql` type cannot hold (text outside an `ENUM`, an invalid `DATE`, structured YAML under a scalar column) is now reported as `OKF023` instead of silently becoming `NULL` in the typed table (#316, #319).
- `check --require-spec` reports it as a warning, and as an error with `--normative-spec`. `sql` and `duckdb --spec-template` record it in `okf.diagnostics` as a warning.
