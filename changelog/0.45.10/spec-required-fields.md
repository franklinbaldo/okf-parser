---
type: Release Note
title: Spec-declared required fields
---

# Spec-declared required fields

`okf-parser check` now reads a type specification's `## Required fields`
Markdown table when `--require-spec` is enabled. Missing or blank frontmatter
fields are reported as `OKF011`; `--normative-spec` promotes them to
conformance errors.

This lets producer specs drive agent fill-until-green workflows without a
domain-specific form CLI.
