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

        table_name = create_statements[0].query.strip().split(None, 3)[2].strip('"')
        tables = con.execute(
            "SELECT table_name, comment FROM duckdb_tables() WHERE table_name = ?",
            [table_name],
        ).fetchall()
        if not tables:
            message = f"declared schema's table {table_name!r} was not found after creation"
            raise DeclaredSchemaError(message)
        table_comment = tables[0][1]

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
