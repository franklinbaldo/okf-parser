---
type: Release Note
title: Restore green main after the list-field apply merge
---

- Apply `ruff format` to the files left unformatted by the list-field `apply` change and drop four unused `noqa` directives in `scripts/cargo_timings.py`, so the `quality` gate passes on `main` again. No behavior change.
