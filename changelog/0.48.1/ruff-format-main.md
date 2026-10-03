---
type: Release Note
title: Restore green main after the list-field apply merge
---

- Apply `ruff format` to the files left unformatted by the list-field `apply` change and drop four unused `noqa` directives in `scripts/cargo_timings.py`, so the `quality` gate passes on `main` again. No behavior change.
- Align `test_frontmatter_the_old_round_trip_check_refused_is_now_edited_losslessly` with the engine and the rest of the suite: a plain scalar written by `apply` stays plain (`setor: FSB`), not quoted. The test never ran on the list-field merge because the formatting gate failed first.
