---
type: Release Note
title: Parser/validator boundary regression tests
---

# Parser/validator boundary regression tests

- Added `tests/test_parser_validator_boundary.py`, pinning down that
  `parse_document` stays permissive (missing or unrecognized `type`, unknown
  frontmatter fields) while `load_bundle` is the sole layer emitting
  normative diagnostics such as `OKF002`.
- Cross-referenced the new tests from `docs/architecture.md`.
