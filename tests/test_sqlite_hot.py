"""Tests for the SQLite physical materialization target."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import duckdb
import pytest

from okf_parser.materialization import materialize_sqlite_hot, open_sqlite_memory_copy

if TYPE_CHECKING:
    from pathlib import Path


def _source() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA okf")
    connection.execute(
        "CREATE TABLE okf.concepts AS SELECT * FROM (VALUES "
        "('a', 'a', 'a.md', 'Node', 'A', NULL, 'sha256:a', 'parsed:a'), "
        "('b', 'b', 'b.md', 'Node', 'B', NULL, 'sha256:b', 'parsed:b')) "
        "AS t(concept_id, logical_key, path, concept_type, title, description, "
        "source_digest, parsed_digest)"
    )
    connection.execute(
        "CREATE TABLE okf.links AS SELECT * FROM (VALUES "
        "('a', 'b.md', 'b', true, 'body')) "
        "AS t(source_id, raw_target, target_id, exists, origin)"
    )
    return connection


def test_materialize_sqlite_hot_uses_duckdb_cross_database_transfer(tmp_path: Path) -> None:
    destination = tmp_path / "hot.sqlite"
    source = _source()
    try:
        result = materialize_sqlite_hot(source, destination)
    finally:
        source.close()

    assert result == {
        "path": str(destination),
        "concept_count": 2,
        "link_count": 1,
    }
    hot = sqlite3.connect(destination)
    try:
        assert hot.execute(
            "SELECT concept_id, title FROM concepts WHERE concept_id = ?", ("b",)
        ).fetchone() == ("b", "B")
        assert hot.execute(
            "SELECT target_id FROM links WHERE source_id = ?", ("a",)
        ).fetchone() == ("b",)
        concept_indexes = {row[1] for row in hot.execute("PRAGMA index_list(concepts)")}
        link_indexes = {row[1] for row in hot.execute("PRAGMA index_list(links)")}
        assert "concepts_concept_id_idx" in concept_indexes
        assert {"links_source_id_idx", "links_target_id_idx"} <= link_indexes
    finally:
        hot.close()


def test_open_sqlite_memory_copy_preserves_materialized_rows(tmp_path: Path) -> None:
    destination = tmp_path / "hot.sqlite"
    source = _source()
    try:
        materialize_sqlite_hot(source, destination)
    finally:
        source.close()

    memory = open_sqlite_memory_copy(destination)
    try:
        assert memory.execute(
            "SELECT path FROM concepts WHERE concept_id = ?", ("a",)
        ).fetchone() == ("a.md",)
        assert memory.execute(
            "SELECT source_id FROM links WHERE target_id = ?", ("b",)
        ).fetchone() == ("a",)
    finally:
        memory.close()


def test_materialize_sqlite_hot_refuses_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "hot.sqlite"
    destination.write_bytes(b"already here")
    source = _source()
    try:
        with pytest.raises(FileExistsError):
            materialize_sqlite_hot(source, destination)
    finally:
        source.close()


def test_materialize_sqlite_hot_rejects_unsafe_schema_name(tmp_path: Path) -> None:
    source = _source()
    try:
        with pytest.raises(ValueError, match="invalid DuckDB schema"):
            materialize_sqlite_hot(
                source,
                tmp_path / "hot.sqlite",
                schema='okf"; DROP TABLE okf.concepts; --',
            )
    finally:
        source.close()
