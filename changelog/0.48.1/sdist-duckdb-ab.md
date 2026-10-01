---
type: Release Note
title: Paired sdist DuckDB build experiment
---

- Add an opt-in CI experiment that builds the same source distribution with bundled DuckDB and with the pinned external libduckdb on one runner using isolated Cargo targets.
- Pre-fetch only the Cargo registry and install Python dependencies outside the timed region so the paired result isolates native build cost without changing the canonical cold sdist release gate.
