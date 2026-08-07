"""Read a type's optional declared DuckDB SQL, per RFC 0006's trust model.

An optional ``.schema.sql`` file may sit beside a type's specification
document, at a path *derived* the same way `type_specs.py` derives the spec
document's own path - no `schema:` frontmatter field, for the same reason: a
declared path would be a second fact free to disagree with the first.

``.schema.sql`` is trusted DuckDB SQL, not a restricted data format: CTAS,
joins, `read_csv`/`read_json`, macros, temp tables, generated columns -
anything a dedicated DuckDB connection accepts is accepted here too, run
whole. Nothing in this module inspects statement shape or type; only the
*post-condition* is checked afterward, against the connection's own
catalog: exactly one non-temporary table named for the concept type must
exist, with a queryable schema. Auxiliary tables the script created along
the way are not part of the contract and are ignored. Never run this
against a `.schema.sql` from a bundle you would not otherwise trust to
execute arbitrary code - it carries the same power as a Makefile or a
migration script, not the safety of a JSON Schema document.
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
    """Raised when a `.schema.sql` script fails, or its post-condition doesn't hold."""


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


def _duckdb_identifier_key(identifier: str) -> str:
    """Fold ASCII letters the same way DuckDB resolves quoted identifiers."""
    return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in identifier)


def parse_declared_schema(sql_text: str, concept_type: str) -> DeclaredSchema:
    """Run a `.schema.sql` script whole, then check only its post-condition.

    The whole file is handed to one dedicated in-memory connection in a
    single `execute()` call - DuckDB's own parser, binder, and planner
    decide what it means and in what order its statements run; nothing here
    re-derives or restricts that. Afterward, the connection's own catalog
    (`duckdb_tables()`, `duckdb_columns()`) must show exactly one
    non-temporary table whose identifier resolves as `concept_type` - table
    identity follows DuckDB's ASCII case-insensitive identifier semantics,
    while the authored spelling read back from the catalog is preserved.
    Any other table the script created (a staging table, a join source) is
    simply not looked at.
    """
    con = duckdb.connect()
    try:
        try:
            con.execute(sql_text)
        except duckdb.Error as exc:
            message = f"declared schema script failed: {exc}"
            raise DeclaredSchemaError(message) from exc

        catalog_tables = con.execute(
            "SELECT database_oid, schema_oid, table_oid, table_name, comment "
            "FROM duckdb_tables() WHERE NOT temporary"
        ).fetchall()
        expected_key = _duckdb_identifier_key(concept_type)
        tables = [
            row for row in catalog_tables if _duckdb_identifier_key(str(row[3])) == expected_key
        ]
        if not tables:
            message = f"declared schema script did not leave behind a table named {concept_type!r}"
            raise DeclaredSchemaError(message)
        if len(tables) > 1:
            message = f"declared schema script left more than one table named {concept_type!r}"
            raise DeclaredSchemaError(message)
        database_oid, schema_oid, table_oid, table_name, table_comment = tables[0]

        columns = con.execute(
            "SELECT column_name, data_type, comment FROM duckdb_columns() "
            "WHERE database_oid = ? AND schema_oid = ? AND table_oid = ? "
            "ORDER BY column_index",
            [database_oid, schema_oid, table_oid],
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
