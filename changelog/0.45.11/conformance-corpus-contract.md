---
type: Release Note
title: Conformance corpus is now a tracked, gated compatibility contract
---

# Conformance corpus is now a tracked, gated compatibility contract

`conformance/MANIFEST.json` now indexes every fixture file under
`conformance/`, records which OKF spec revision (an upstream commit SHA
against `okf/SPEC.md`) the corpus was last checked against, and states which
language suites (Python, TypeScript) each fixture is shared with.
`tests/test_conformance_manifest.py` fails if the manifest and the directory
ever drift apart.

`conformance/regressions.json` adds a permanent, append-only home for
fixtures written against concrete reported bugs, so a fix can't silently
regress once the hand-written test around it is refactored away.

The release workflow (`publish.yml`) now runs the Python and TypeScript
conformance corpora in a dedicated `conformance` job before any wheel is
built, so a release can no longer ship a cross-language compatibility
regression that CI on `main` happened to miss.

See `conformance/README.md` for the corpus's contribution and versioning
conventions.
