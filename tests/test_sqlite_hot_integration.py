"""Integration test for SQLite physical materialization from canonical relations."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import duckdb

from okf_parser.materialization import materialize_sqlite_hot
from okf_parser.service import export_duckdb

if TYPE_CHECKING:
    from pathlib import Path


def test_sqlite_target_consumes_exported_relations(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a.md").write_text(
        "---\ntype: Node\ntitle: A\n---\n\n# A\n\n[B](b.md)\n",
        encoding="utf-8",
    )
    (bundle / "b.md").write_text(
        "---\ntype: Node\ntitle: B\n---\n\n# B\n",
        encoding="utf-8",
    )

    database = tmp_path / "knowledge.duckdb"
    export_duckdb(str(bundle), str(database))
    connection = duckdb.connect(database)
    destination = tmp_path / "hot.sqlite"
    try:
        connection.execute("INSTALL sqlite")
        connection.execute("LOAD sqlite")
        materialize_sqlite_hot(connection, destination)
    finally:
        connection.close()

    hot = sqlite3.connect(destination)
    try:
        assert hot.execute("SELECT path FROM concepts WHERE concept_id = ?", ("a",)).fetchone() == (
            "a.md",
        )
        assert hot.execute(
            "SELECT target_id FROM links WHERE source_id = ?", ("a",)
        ).fetchone() == ("b",)
    finally:
        hot.close()
