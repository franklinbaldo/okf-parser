# Conformance corpus

This directory is the versioned, executable compatibility contract between
`okf-parser` and the [Open Knowledge Format
specification](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md),
and between the Python, TypeScript, and Rust implementations of this parser.
Every fixture here is data, not implementation: each language's test suite
loads the same JSON and asserts the same expectations, so the three
implementations cannot silently drift from each other while still passing
their own hand-written tests.

`MANIFEST.json` is the index. It records:

- which upstream spec revision (`spec.revision`, a commit SHA against
  `okf/SPEC.md`) the corpus was last checked against;
- the list of fixture files, what area of behavior each covers, and which
  language suites consume it (`shared_with`).

`tests/test_conformance_manifest.py` enforces that the manifest and the
directory never drift apart: every `.json` file in `conformance/` must be
listed, and every listed file must exist.

## Adding or changing a fixture

1. Add cases to the relevant `conformance/*.json` file (or add a new file and
   register it in `MANIFEST.json`).
2. If the change tracks a new upstream spec revision, update
   `spec.revision` and `spec.revision_date` in `MANIFEST.json` and bump
   `corpus_version`.
3. Add or update the loader test(s) in each language's suite that reads the
   fixture (Python under `tests/`, TypeScript under `typescript/test/`).

## Regressions (`regressions.json`)

`regressions.json` is append-only: once a case lands, it is never edited or
removed, even if the surrounding code is later rewritten. This is what makes
a fixed bug stay fixed — a future change that reintroduces the old behavior
fails a fixture written specifically against it, not a hand-written test that
may have been deleted along with the code it covered.

When you fix a bug that a user could hit (as opposed to an internal
refactor), add a case with:

- `id` — a short, stable, kebab-case identifier;
- `issue` — the GitHub issue or PR number that reported or fixed it;
- `input` — the minimal reproduction;
- `expected` — the correct behavior.

Cases are grouped by the area they exercise so each language suite can load
only the areas it implements.
