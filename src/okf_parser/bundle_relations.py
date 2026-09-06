"""Execute trusted bundle-level relation SQL over materialized OKF type tables."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

RELATIONS_FILENAME = "okf.relations.sql"
RELATIONS_SCHEMA = "okf_relations"


class BundleRelationsError(ValueError):
    """Raised when bundle relation SQL cannot be read, executed, or inspected."""


@dataclass(frozen=True, slots=True)
class BundleRelationCatalog:
    """Relation objects published by one bundle-level SQL program."""

    schema: str
    relations: tuple[str, ...]


def execute_bundle_relations(
    connection: DuckDBPyConnection,
    root: str | Path,
    *,
    filename: str = RELATIONS_FILENAME,
) -> BundleRelationCatalog:
    """Run one optional trusted relation program in the typed-table connection.

    The caller must materialize `okf_types` before invoking this function.
    Missing relation SQL is a valid empty relation catalog. When the file is
    present, the parser creates the reserved `okf_relations` schema first and
    then hands the SQL text to DuckDB whole.
    """
    path = Path(root) / filename
    if not path.is_file():
        return BundleRelationCatalog(schema=RELATIONS_SCHEMA, relations=())

    try:
        sql_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        message = f"could not read bundle relation SQL {path}: {exc}"
        raise BundleRelationsError(message) from exc

    connection.execute("CREATE SCHEMA IF NOT EXISTS okf_relations")
    try:
        connection.execute(sql_text)
    except duckdb.Error as exc:
        message = f"bundle relation SQL failed: {exc}"
        raise BundleRelationsError(message) from exc

    schema_exists = connection.execute(
        "SELECT count(*) FROM information_schema.schemata WHERE schema_name = ?",
        [RELATIONS_SCHEMA],
    ).fetchone()
    if schema_exists is None or int(schema_exists[0]) != 1:
        message = f"bundle relation SQL removed reserved schema {RELATIONS_SCHEMA!r}"
        raise BundleRelationsError(message)

    rows = connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = ? ORDER BY table_name",
        [RELATIONS_SCHEMA],
    ).fetchall()
    return BundleRelationCatalog(
        schema=RELATIONS_SCHEMA,
        relations=tuple(str(row[0]) for row in rows),
    )


__all__ = [
    "BundleRelationCatalog",
    "BundleRelationsError",
    "RELATIONS_FILENAME",
    "RELATIONS_SCHEMA",
    "execute_bundle_relations",
]
