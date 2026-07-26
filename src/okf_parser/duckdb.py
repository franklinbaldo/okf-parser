"""Materialize an OKF bundle as ordinary DuckDB tables."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import ibis

from okf_parser.bundle import Bundle, load_bundle

if TYPE_CHECKING:
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


def _validate_schema_name(schema: str) -> None:
    if _IDENTIFIER_RE.fullmatch(schema) is None:
        msg = f"invalid DuckDB schema name: {schema!r}"
        raise ValueError(msg)


def _diagnostics_table(bundle: Bundle) -> ibis.Table:
    rows = [
        {
            "code": item.code,
            "severity": item.severity.value,
            "path": item.path,
            "message": item.message,
        }
        for item in bundle.validate()
    ]
    return ibis.memtable(rows, schema=_DIAGNOSTIC_SCHEMA)


def attach_okf(
    connection: duckdb.DuckDBPyConnection,
    path: str | Path,
    *,
    schema: str = "okf",
) -> dict[str, object]:
    """Materialize one OKF bundle into a DuckDB schema.

    The function creates four ordinary tables inside ``schema``:
    ``concepts``, ``links``, ``reserved``, and ``diagnostics``. Once copied,
    the tables are independent of Python and remain queryable from any
    DuckDB client that opens the database.
    """
    _validate_schema_name(schema)
    bundle = load_bundle(Path(path))
    relations = {
        "concepts": bundle.concepts,
        "links": bundle.links,
        "reserved": bundle.reserved,
        "diagnostics": _diagnostics_table(bundle),
    }

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        for table_name, relation in relations.items():
            qualified_name = f"{schema}.{table_name}"
            connection.from_arrow(relation.to_pyarrow()).create(qualified_name)
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
