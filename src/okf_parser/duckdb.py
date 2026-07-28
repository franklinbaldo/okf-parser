"""Materialize an OKF bundle as ordinary DuckDB tables."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import ibis

from okf_parser.bundle import Bundle, load_bundle

if TYPE_CHECKING:
    from collections.abc import Sequence

    import duckdb

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DIAGNOSTIC_SCHEMA = ibis.schema(
    {
        "code": "string",
        "severity": "string",
        "path": "string",
        "message": "string",
    }
)


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


def _diagnostics_table(bundle: Bundle) -> ibis.Table:
    rows = [item.model_dump(mode="json") for item in bundle.validate()]
    return ibis.memtable(rows, schema=_DIAGNOSTIC_SCHEMA)


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
) -> dict[str, object]:
    """Materialize one OKF bundle into a DuckDB schema.

    The function creates four ordinary tables inside ``schema``:
    ``concepts``, ``links``, ``reserved``, and ``diagnostics``. Once copied,
    the tables are independent of Python and remain queryable from any
    DuckDB client that opens the database.

    Materializing twice into the same schema raises :class:`BundleExportError`
    unless ``overwrite`` is set, in which case the four tables are replaced.
    """
    _validate_schema_name(schema)
    bundle = load_bundle(Path(path), exclude)
    relations = {
        "concepts": bundle.concepts,
        "links": bundle.links,
        "reserved": bundle.reserved,
        "diagnostics": _diagnostics_table(bundle),
    }

    collisions = _existing_tables(connection, schema, tuple(relations))
    if collisions and not overwrite:
        raise BundleExportError(schema, collisions)

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        for table_name, relation in relations.items():
            _replace_table(connection, schema, table_name, relation)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    return {
        "schema": schema,
        "root": str(bundle.root),
        "conformant": bundle.is_conformant,
        "markdown_count": bundle.markdown_count,
        "concept_count": cast("int", bundle.concepts.count().execute()),
        "link_count": cast("int", bundle.links.count().execute()),
        "diagnostic_count": len(bundle.diagnostics),
    }


def _replace_table(
    connection: duckdb.DuckDBPyConnection,
    schema: str,
    table_name: str,
    relation: ibis.Table,
) -> None:
    """Create one table from an Ibis relation, dropping any earlier copy.

    The DuckDB relation API has no replace mode, so an existing table is
    dropped first. Both statements run inside the caller's transaction, so a
    failure rolls the drop back with everything else.
    """
    connection.execute(f'DROP TABLE IF EXISTS "{schema}"."{table_name}"')
    connection.from_arrow(relation.to_pyarrow()).create(f"{schema}.{table_name}")
