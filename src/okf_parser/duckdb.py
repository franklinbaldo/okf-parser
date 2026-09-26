"""Materialize an OKF bundle as ordinary DuckDB tables."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from okf_parser.bundle import load_bundle
from okf_parser.typed_tables import (
    TypedTableCollisionError,
    discover_declared_schemas,
    materialize_typed_tables,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import duckdb

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VARCHAR = "VARCHAR"
_TABLE_COLUMNS: dict[str, dict[str, str]] = {
    "concepts": dict.fromkeys(
        (
            "concept_id",
            "logical_key",
            "path",
            "concept_type",
            "title",
            "description",
            "source_digest",
            "parsed_digest",
            "frontmatter_json",
            "body",
        ),
        _VARCHAR,
    ),
    "links": {
        "source_id": _VARCHAR,
        "raw_target": _VARCHAR,
        "target_id": _VARCHAR,
        "exists": "BOOLEAN",
        "origin": _VARCHAR,
    },
    "reserved": dict.fromkeys(("path", "filename", "body"), _VARCHAR),
    "diagnostics": dict.fromkeys(("code", "severity", "path", "message"), _VARCHAR),
}
"""Column order and DuckDB types of the four exported tables."""


class BundleExportError(ValueError):
    """Raised when a bundle cannot be materialized into a DuckDB schema."""

    def __init__(self, schema_name: str, tables: tuple[str, ...]) -> None:
        """Record which schema already holds which of the bundle's tables."""
        self.schema_name = schema_name
        self.tables = tables
        listed = ", ".join(tables)
        super().__init__(
            f"schema {schema_name!r} already contains {listed}; "
            f"pass overwrite=True or choose another database or schema"
        )


def _validate_schema_name(schema: str) -> None:
    if _IDENTIFIER_RE.fullmatch(schema) is None:
        msg = f"invalid DuckDB schema name: {schema!r}"
        raise ValueError(msg)


def _existing_tables(
    connection: duckdb.DuckDBPyConnection,
    schema: str,
    names: tuple[str, ...],
) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
        [schema],
    ).fetchall()
    present = {str(row[0]) for row in rows}
    return tuple(name for name in names if name in present)


def attach_okf(
    connection: duckdb.DuckDBPyConnection,
    path: str | Path,
    *,
    schema: str = "okf",
    overwrite: bool = False,
    exclude: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, object]:
    """Materialize one OKF bundle into a DuckDB schema.

    The function creates four ordinary tables inside ``schema``:
    ``concepts``, ``links``, ``reserved``, and ``diagnostics``. Once copied,
    the tables are independent of Python and remain queryable from any
    DuckDB client that opens the database.

    Materializing twice into the same schema raises :class:`BundleExportError`
    unless ``overwrite`` is set, in which case the four tables are replaced.
    With ``spec_template``, declared concept types are additionally persisted
    in ``{schema}_types`` as raw columns plus stable typed snapshot columns.
    """
    _validate_schema_name(schema)
    bundle = load_bundle(Path(path), exclude)
    typed_schema = f"{schema}_types"
    relations: dict[str, list[dict[str, object]]] = {
        "concepts": [concept.model_dump() for concept in bundle.concepts],
        "links": [link.model_dump() for link in bundle.links],
        "reserved": [reserved.model_dump() for reserved in bundle.reserved],
        "diagnostics": [item.model_dump(mode="json") for item in bundle.validate()],
    }

    collisions = _existing_tables(connection, schema, tuple(relations))
    if collisions and not overwrite:
        raise BundleExportError(schema, collisions)

    declarations = discover_declared_schemas(
        bundle.root,
        bundle.concept_types,
        spec_template,
    )

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        for table_name, rows in relations.items():
            _replace_table(connection, schema, table_name, rows)
        typed = None
        if spec_template is not None:
            try:
                typed = materialize_typed_tables(
                    connection,
                    bundle,
                    schema=typed_schema,
                    declarations=declarations,
                    overwrite=overwrite,
                )
            except TypedTableCollisionError as exc:
                raise BundleExportError(exc.schema_name, exc.tables) from exc
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    result: dict[str, object] = {
        "schema": schema,
        "root": str(bundle.root),
        "conformant": bundle.is_conformant,
        "markdown_count": bundle.markdown_count,
        "concept_count": len(bundle.concepts),
        "link_count": len(bundle.links),
        "diagnostic_count": len(bundle.diagnostics),
    }
    if spec_template is not None and typed is not None:
        result.update(
            {
                "typed_schema": typed.schema,
                "typed_table_count": len(typed.tables),
                "typed_tables": list(typed.tables),
                "unrecognized_type_tables": list(typed.unrecognized_tables),
            }
        )
    return result


def _replace_table(
    connection: duckdb.DuckDBPyConnection,
    schema: str,
    table_name: str,
    rows: Sequence[Mapping[str, object]],
) -> None:
    """Create one table from records, dropping any earlier copy.

    Rows load column by column - one ``unnest`` per column in a single
    ``INSERT`` - so no dataframe library is involved. Both statements run
    inside the caller's transaction, so a failure rolls the drop back with
    everything else.
    """
    columns = _TABLE_COLUMNS[table_name]
    qualified = f'"{schema}"."{table_name}"'
    definition = ", ".join(f'"{column}" {kind}' for column, kind in columns.items())
    connection.execute(f"DROP TABLE IF EXISTS {qualified}")
    connection.execute(f"CREATE TABLE {qualified} ({definition})")
    if not rows:
        return
    selections = ", ".join(
        f"unnest(${index})::{kind}" for index, kind in enumerate(columns.values(), start=1)
    )
    values = [[row.get(column) for row in rows] for column in columns]
    connection.execute(f"INSERT INTO {qualified} SELECT {selections}", values)
