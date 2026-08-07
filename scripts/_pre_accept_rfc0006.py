from pathlib import Path

path = Path("rfcs/0006-declared-column-types.md")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    if old not in text:
        raise SystemExit(f"expected residual RFC text not found: {old[:120]!r}")
    text = text.replace(old, new, 1)


replace_once(
    "- **Drift detection.** A declared type is a checkable claim. When a document\n  starts carrying `\"n/a\"` in an integer field, something should say so —\n  today nothing can, because nothing was ever claimed.",
    "- **Drift visibility.** A declared type is a checkable claim. When a document\n  starts carrying `\"n/a\"` in an integer field, the typed projection and raw\n  carrier make the mismatch queryable instead of silently changing the column's\n  physical meaning.",
)
replace_once(
    "  declaration is free to use a type outside it (the declaration stays\n  well-formed; that one column simply isn't typed).",
    "  declaration may use a type outside the first target-mapped families; its\n  normalized DuckDB spelling still survives in the lossless IR, and each target\n  decides how much of that physical type it can represent truthfully.",
)
replace_once(
    "- **DuckDB's own normalized catalog is the one intermediate representation,\n  not the declaration's source text.** What materializes on `apply`'s\n  ephemeral table and `duckdb`'s persistent one is never the declaration\n  script's `CREATE TABLE` re-run verbatim — it is a *different*\n  `CREATE TABLE` the compiler synthesizes, one raw `VARCHAR`/`VARCHAR[]`\n  column and one generated column per declared field, built from the name\n  and type this decision's post-condition read out of the resulting\n  table's catalog entry. The declaration is authoritative for *name* and\n  *type*; the physical table shape is the compiler's, always. This is a\n  translation with exactly one well-specified input (a `(name, type)` pair\n  per column) and one well-specified output (decision 5's pair) — narrow\n  enough that \"cannot drift\" still holds, regardless of how elaborate the\n  script that produced the input was.",
    "- **DuckDB's own normalized catalog is the one intermediate representation,\n  not the declaration's source text.** What materializes on `apply`'s\n  ephemeral table and `duckdb`'s persistent one is never the declaration\n  script's `CREATE TABLE` re-run verbatim. Both consumers compile the same\n  raw-plus-typed logical plan from catalog `(name, type)` pairs: `apply`\n  realizes the typed side as a virtual generated projection, while persistent\n  `duckdb` stores it as an ordinary snapshot column. The declaration is\n  authoritative for public name and physical type; consumer-specific storage\n  remains compiler-owned.",
)
replace_once(
    "— the declaration is how they find out it isn't. `--fail-on-spec-divergence`\n(decision 8) gives the strict posture to whoever wants it, opt-in.",
    "— the declaration is how they expose that mismatch. A future validation or\nquality-policy layer may choose to reject such rows, but that is intentionally\nseparate from this RFC's type-materialization contract.",
)
replace_once(
    "Rejected for v1, though it is the honest alternative to decision 1's\ncollision diagnostic, since matching an exact `type` value avoids slug",
    "Rejected for v1, though it is the honest alternative to decision 1's\ncollision error, since matching an exact `type` value avoids slug",
)
replace_once(
    "- Exact `OKF0xx` codes for decision 7's cases — the existing numbering runs\n  through `OKF010`, and the assignment should be made against\n  `bundle.py`/`type_specs.py` rather than fixed here in isolation.\n",
    "",
)
replace_once(
    "- Extending decision 5a's type set — each addition needs both a cast\n  predicate (decision 5) and an export mapping (decision 10), which is the\n  deliberate cost of adding one.",
    "- Extending decision 5a's target mappings — DuckDB can preserve additional\n  physical types in the IR, but each exporter still needs an explicit truthful\n  representation policy (decision 10).",
)
replace_once(
    "  by asking DuckDB's own `TRY_CAST` — the same all-or-nothing test decision\n  5 uses at *check* time, reused at *propose* time (`infer_kinds_via_duckdb`):",
    "  by asking DuckDB's own `TRY_CAST` through `infer_kinds_via_duckdb`. This\n  is deliberately a conservative *proposal* heuristic, separate from decision\n  5's per-value runtime materialization:",
)

path.write_text(text, encoding="utf-8")
