"""Materialize canonical relations into workload-specific physical targets.

DuckDB is the transformation/query engine at this boundary, not a required
persisted representation. Physical targets are derived from canonical
relations and may be discarded or rebuilt without changing OKF semantics.

SQLite is currently the first proven target: it is useful for indexed point
lookup and adjacency. Other targets such as Parquet or Arrow should be added
as sibling materializers only when their workloads are implemented and
benchmarked.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQLITE_ALIAS = "__okf_materialized_sqlite"


def _validate_schema_name(schema: str) -> None:
    if _IDENTIFIER_RE.fullmatch(schema) is None:
        msg = f"invalid DuckDB schema name: {schema!r}"
        raise ValueError(msg)


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def materialize_sqlite_hot(
    connection: duckdb.DuckDBPyConnection,
    destination: str | Path,
    *,
    schema: str = "okf",
    overwrite: bool = False,
) -> dict[str, int | str]:
    """Materialize a SQLite target optimized for point and adjacency reads.

    DuckDB performs the cross-database transfer through its SQLite extension.
    The resulting database is a derived physical representation: it contains
    no parsing, identity, link-resolution, or other OKF semantic logic.
    """
    _validate_schema_name(schema)
    target = Path(destination)
    if target.exists():
        if not overwrite:
            raise FileExistsError(target)
        target.unlink()

    quoted_schema = f'"{schema}"'
    quoted_path = _sql_string(str(target.resolve()))
    connection.execute(f"ATTACH {quoted_path} AS {_SQLITE_ALIAS} (TYPE sqlite)")
    try:
        connection.execute(
            f"CREATE TABLE {_SQLITE_ALIAS}.concepts AS "  # noqa: S608 - validated identifier.
            "SELECT concept_id, logical_key, path, concept_type, title, description, "
            "source_digest, parsed_digest "
            f"FROM {quoted_schema}.concepts"
        )
        connection.execute(
            f"CREATE TABLE {_SQLITE_ALIAS}.links AS "  # noqa: S608 - validated identifier.
            "SELECT source_id, raw_target, target_id, exists, origin "
            f"FROM {quoted_schema}.links"
        )
    finally:
        connection.execute(f"DETACH {_SQLITE_ALIAS}")

    materialized = sqlite3.connect(target)
    try:
        materialized.executescript(
            "CREATE UNIQUE INDEX concepts_concept_id_idx ON concepts(concept_id);"
            "CREATE INDEX links_source_id_idx ON links(source_id);"
            "CREATE INDEX links_target_id_idx ON links(target_id) WHERE target_id IS NOT NULL;"
        )
        materialized.commit()
        concept_count = int(materialized.execute("SELECT count(*) FROM concepts").fetchone()[0])
        link_count = int(materialized.execute("SELECT count(*) FROM links").fetchone()[0])
    finally:
        materialized.close()

    return {
        "path": str(target),
        "concept_count": concept_count,
        "link_count": link_count,
    }


def open_sqlite_memory_copy(source: str | Path) -> sqlite3.Connection:
    """Copy a materialized SQLite target into a private in-memory database."""
    disk = sqlite3.connect(Path(source))
    memory = sqlite3.connect(":memory:")
    try:
        disk.backup(memory)
    except sqlite3.Error:
        memory.close()
        raise
    finally:
        disk.close()
    return memory
