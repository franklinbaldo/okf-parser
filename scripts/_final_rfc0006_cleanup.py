from pathlib import Path

path = Path("rfcs/0006-declared-column-types.md")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    if old not in text:
        raise SystemExit(f"expected final RFC text not found: {old[:120]!r}")
    text = text.replace(old, new, 1)


replace_once(
    "The\ndiagnostic name is corrected to match what is actually known: a table\npresent in `{schema}_types` whose name is not a currently-declared type is\nreported as **unrecognized**, and `duckdb` does not remove it.",
    "The\nreport uses the only state that is actually known: a table present in\n`{schema}_types` whose name is not a currently-declared type is reported as\n**unrecognized**, and `duckdb` does not remove it.",
)
replace_once(
    "This matches decision 5's per-value materialization: the future typed\ncolumn remains typed and only that row's `TRY_CAST` result becomes `NULL`.",
    "This matches decision 5's per-value materialization: the typed column\nremains typed and only that row's `TRY_CAST` result becomes `NULL`.",
)
replace_once(
    "### 11. `init` scaffolds the missing specification documents, never a declaration",
    "### 11. `init` scaffolds specification documents and may propose starter declarations",
)
replace_once(
    "`--require-spec`/`--spec-template` (decision 1) only ever *reports* a\nmissing `docs/types/{slug}.md`; nothing in RFC 0005 or this RFC creates\none. A bundle adopting either rule for the first time can have dozens of\ntypes in use and zero specification documents, and hand-creating each one\nat its exact derived path is exactly the kind of bookkeeping this RFC\nalready refuses to make an author do by hand elsewhere (decision 1's whole\npremise is that the path is *computable*, not authored).",
    "`check --require-spec` only reports missing specification documents; `init` is\nthe explicit authoring surface that can fill those gaps. A bundle adopting the\nconvention for the first time can have dozens of types in use and zero specification\ndocuments, and hand-creating each one at its exact derived path is exactly the kind\nof bookkeeping decision 1 makes computable instead of authored.",
)
replace_once(
    "### 12. `init` --require-spec derived-path collisions raise before writing anything",
    "### 12. `init` derived-path collisions raise before writing anything",
)

path.write_text(text, encoding="utf-8")
