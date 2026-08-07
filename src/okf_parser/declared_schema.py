"""Read a type's optional declared DuckDB DDL, per RFC 0006.

An optional ``.schema.sql`` file may sit beside a type's specification
document, at a path *derived* the same way `type_specs.py` derives the spec
document's own path - no `schema:` frontmatter field, for the same reason: a
declared path would be a second fact free to disagree with the first.

The file must contain exactly one ``CREATE TABLE`` statement, optionally
followed by ``COMMENT ON`` statements. It is never hand-parsed: DuckDB's own
`extract_statements()` validates its shape and an in-memory connection's
catalog (`duckdb_columns()`, `duckdb_tables()`) is the parsed representation,
so whatever DuckDB 1.5.5 accepts as valid DDL is accepted here too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import duckdb

from okf_parser.type_specs import spec_relative_path

if TYPE_CHECKING:
    from okf_parser.schema_lexemes import CastKind

_DUCKDB_TYPE_KINDS: dict[str, CastKind] = {
    "BOOLEAN": "boolean",
    "TINYINT": "integer",
    "SMALLINT": "integer",
    "INTEGER": "integer",
    "BIGINT": "integer",
    "HUGEINT": "integer",
    "UTINYINT": "integer",
    "USMALLINT": "integer",
    "UINTEGER": "integer",
    "UBIGINT": "integer",
    "UHUGEINT": "integer",
    "FLOAT": "number",
    "DOUBLE": "number",
    "REAL": "number",
    "DATE": "date",
    "TIMESTAMP": "datetime",
    "TIMESTAMP WITH TIME ZONE": "datetime",
    "TIMESTAMPTZ": "datetime",
    "VARCHAR": "string",
}


class DeclaredSchemaError(ValueError):
    """Raised when a `.schema.sql` file does not have the RFC 0006 shape."""


@dataclass(frozen=True, slots=True)
class DeclaredSchema:
    """One type's declared columns, decoded from its `.schema.sql` catalog entry."""

    table_name: str
    columns: dict[str, str]
    table_comment: str | None
    column_comments: dict[str, str]


def declared_schema_relative_path(spec_template: str, concept_type: str) -> str | None:
    """Derive the `.schema.sql` path beside a type's specification document.

    Mirrors `type_specs.spec_relative_path`, then swaps the document's own
    extension for `.schema.sql` - the same derived-not-declared relationship
    the spec document itself has to `type`.
    """
    relative = spec_relative_path(spec_template, concept_type)
    if relative is None:
        return None
    stem = relative.rsplit(".", 1)[0] if "." in relative.rsplit("/", 1)[-1] else relative
    return f"{stem}.schema.sql"


def _duckdb_type_kind(duckdb_type: str) -> CastKind | None:
    normalized = duckdb_type.strip().upper()
    if normalized.startswith("DECIMAL"):
        return "number"
    return _DUCKDB_TYPE_KINDS.get(normalized)


def parse_declared_schema(sql_text: str) -> DeclaredSchema:
    """Decode one `.schema.sql` file's single `CREATE TABLE` and its comments.

    Executed against a throwaway in-memory connection so DuckDB's own parser,
    binder, and catalog do the work; nothing here re-derives what the DDL
    means. Exactly one `CREATE TABLE` is allowed (no CTAS), with zero or more
    `COMMENT ON` statements naming that same table or one of its columns.
    """
    con = duckdb.connect()
    try:
        try:
            statements = con.extract_statements(sql_text)
        except duckdb.Error as exc:
            message = f"declared schema could not be parsed: {exc}"
            raise DeclaredSchemaError(message) from exc
        create_statements = [
            s
            for s in statements
            if str(s.type).endswith("CREATE") and s.query.strip().upper().startswith("CREATE TABLE")
        ]
        if len(create_statements) != 1:
            message = (
                "declared schema must contain exactly one CREATE TABLE statement, "
                f"found {len(create_statements)}"
            )
            raise DeclaredSchemaError(message)
        for statement in statements:
            query = statement.query.strip().rstrip(";")
            is_create = statement in create_statements
            is_comment = str(statement.type).endswith("ALTER") and query.upper().startswith(
                "COMMENT ON"
            )
            if not (is_create or is_comment):
                message = (
                    "declared schema may only contain one CREATE TABLE and "
                    f"COMMENT ON statements, found: {query}"
                )
                raise DeclaredSchemaError(message)
            try:
                con.execute(query)
            except duckdb.Error as exc:
                message = f"declared schema statement failed: {query}: {exc}"
                raise DeclaredSchemaError(message) from exc

        # The table's name is read back from the catalog, never re-derived from
        # the CREATE TABLE text: a quoted identifier can itself contain
        # whitespace (`"Blog Post"`), which a naive token split on the query
        # text would cut in the wrong place. DuckDB's own parser already
        # resolved the real name once, at CREATE time.
        tables = con.execute("SELECT table_name, comment FROM duckdb_tables()").fetchall()
        if len(tables) != 1:
            names = ", ".join(repr(row[0]) for row in tables)
            message = f"declared schema must create exactly one table, found: {names}"
            raise DeclaredSchemaError(message)
        table_name, table_comment = tables[0]

        columns = con.execute(
            "SELECT column_name, data_type, comment FROM duckdb_columns() "
            "WHERE table_name = ? ORDER BY column_index",
            [table_name],
        ).fetchall()
    finally:
        con.close()

    return DeclaredSchema(
        table_name=table_name,
        columns={row[0]: row[1] for row in columns},
        table_comment=table_comment,
        column_comments={row[0]: row[2] for row in columns if row[2] is not None},
    )


def declared_cast_kinds(schema: DeclaredSchema) -> dict[str, CastKind]:
    """Map each declared column to the shared `CastKind` vocabulary.

    A DuckDB type outside the mapped set (a struct, a list, an unsupported
    numeric width) is silently omitted rather than rejected: the column then
    simply has no declared cast, same as if it were absent from the file.
    """
    kinds: dict[str, CastKind] = {}
    for name, duckdb_type in schema.columns.items():
        kind = _duckdb_type_kind(duckdb_type)
        if kind is not None:
            kinds[name] = kind
    return kinds


_DUCKDB_TYPE_FOR_KIND: dict[CastKind, str] = {
    "string": "VARCHAR",
    "boolean": "BOOLEAN",
    "integer": "BIGINT",
    "number": "DOUBLE",
    "date": "DATE",
    "datetime": "TIMESTAMPTZ",
}

# Narrowest to widest: the first type every observed value TRY_CASTs into
# cleanly wins. BOOLEAN before the numeric types so "true"/"false" isn't
# swallowed by some future numeric-ish cast; DATE before TIMESTAMPTZ so a
# column of pure dates doesn't widen just because DuckDB *can* also read a
# date as a timestamp.
#
# `roundtrip` marks a candidate where DuckDB's own TRY_CAST is lossy rather
# than rejecting: `TRY_CAST('10.50' AS BIGINT)` rounds to 11 instead of
# failing, and `TRY_CAST('2026-01-15T09:30:00Z' AS DATE)` silently drops the
# time - both would otherwise win a column decision 5's "never lossy" rule
# says they shouldn't. For those, a value only counts as cast-clean when
# formatting the cast result back to text reproduces the original string
# exactly; DOUBLE and TIMESTAMPTZ don't lose information relative to the
# narrower candidates already tried before them, so a plain non-null check
# is enough.
_INFERENCE_CANDIDATES: tuple[tuple[str, CastKind, bool], ...] = (
    ("BOOLEAN", "boolean", False),
    ("BIGINT", "integer", True),
    ("DOUBLE", "number", False),
    ("DATE", "date", True),
    ("TIMESTAMPTZ", "datetime", False),
)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def infer_kinds_via_duckdb(columns: dict[str, list[str | None]]) -> dict[str, CastKind]:
    """Infer every column's tightest kind in one vectorized DuckDB pass, via `TRY_CAST`.

    Mirrors decision 5's runtime divergence rule exactly, at inference time:
    a candidate type wins a column only when *every* non-null value in it
    TRY_CASTs cleanly - the same all-or-nothing test this RFC already
    applies when *checking* a declared column, now reused to *propose* one.
    One `CREATE TABLE` and one bulk insert hold every column at once, and
    one `SELECT` computes every column's every candidate in a single
    DuckDB round trip - column-at-a-time aggregates, not a Python loop
    issuing its own query per column per candidate. A column with at least
    one non-null value always gets a kind - `"string"` when no narrower
    candidate fits every value - so it is `"string"`, never `None`, that
    means "no meaningful narrowing." Only a column with nothing but `None`s
    (or no rows at all) is omitted entirely; the caller decides what a
    genuinely empty column means.
    """
    if not columns:
        return {}
    con = duckdb.connect()
    try:
        column_defs = ", ".join(f"{_quote(name)} VARCHAR" for name in columns)
        con.execute(f"CREATE TABLE t ({column_defs})")
        # `strict=True` is the row-count check: every column must align to
        # the same document sequence, or zip raises rather than silently
        # truncating to the shortest column.
        rows = list(zip(*columns.values(), strict=True))
        if rows:
            placeholders = ", ".join(["?"] * len(columns))
            con.executemany(f"INSERT INTO t VALUES ({placeholders})", rows)

        def cast_count_expr(column: str, duckdb_type: str, *, roundtrip: bool) -> str:
            cast_expr = f"TRY_CAST({_quote(column)} AS {duckdb_type})"
            if not roundtrip:
                return f"count({cast_expr})"
            return f"count(*) FILTER (WHERE CAST({cast_expr} AS VARCHAR) = {_quote(column)})"

        select_list = ", ".join(
            f"count({_quote(name)}) AS {_quote(f'{name}__n')}, "
            + ", ".join(
                f"{cast_count_expr(name, duckdb_type, roundtrip=roundtrip)} AS "
                f"{_quote(f'{name}__{kind}')}"
                for duckdb_type, kind, roundtrip in _INFERENCE_CANDIDATES
            )
            for name in columns
        )
        cursor = con.execute(f"SELECT {select_list} FROM t")
        (values,) = cursor.fetchall()
        counts = dict(zip((c[0] for c in cursor.description), values, strict=True))

        kinds: dict[str, CastKind] = {}
        for name in columns:
            non_null = counts[f"{name}__n"]
            if not non_null:
                continue
            kinds[name] = "string"
            for _, kind, _roundtrip in _INFERENCE_CANDIDATES:
                if counts[f"{name}__{kind}"] == non_null:
                    kinds[name] = kind
                    break
        return kinds
    finally:
        con.close()


def render_starter_schema_sql(concept_type: str, columns: dict[str, CastKind]) -> str | None:
    """Render a starter `CREATE TABLE` from inferred column kinds, or ``None`` for no columns.

    A one-way trip out of decision 5a's closed type set (`schema
    --infer-types`'s own vocabulary), never a round trip through DuckDB's
    catalog the way `parse_declared_schema` is - so column order here is
    simply the caller's, not read back from a live table.
    """
    if not columns:
        return None

    lines = [
        f"    {_quote(name)} {_DUCKDB_TYPE_FOR_KIND[kind]}"
        for name, kind in sorted(columns.items())
    ]
    body = ",\n".join(lines)
    return f"CREATE TABLE {_quote(concept_type)} (\n{body}\n);\n"
