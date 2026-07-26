"""Integration tests for the DuckDB surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb
import pytest

from okf_parser.duckdb import attach_okf

if TYPE_CHECKING:
    from pathlib import Path


def test_attach_okf_materializes_queryable_tables(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        "---\ntype: Node\nrelated: /b.md\n---\n[B](b.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")
    connection = duckdb.connect()

    result = attach_okf(connection, tmp_path)

    assert result["conformant"]
    assert connection.sql("SELECT count(*) FROM okf.concepts").fetchone() == (2,)
    assert connection.sql("SELECT count(*) FROM okf.links").fetchone() == (2,)
    assert connection.sql("SELECT count(*) FROM okf.diagnostics").fetchone() == (0,)


def test_attach_okf_rejects_unsafe_schema_name(tmp_path: Path) -> None:
    connection = duckdb.connect()

    with pytest.raises(ValueError, match="invalid DuckDB schema"):
        attach_okf(connection, tmp_path, schema='okf"; DROP TABLE x; --')
