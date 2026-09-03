"""Integration test for SQLite physical materialization from canonical relations."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import duckdb

from okf_parser.duckdb import attach_okf
from okf_parser.materialization import materialize_sqlite_hot

if TYPE_CHECKING:
    from pathlib import Path


def test_sqlite_target_consumes_attach_okf_relations(tmp_path: Path) -> None:
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

    connection = duckdb.connect()
    destination = tmp_path / "hot.sqlite"
    try:
        attach_okf(connection, bundle)
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
