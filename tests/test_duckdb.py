"""Integration tests for the DuckDB surface."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import duckdb
import pytest

from okf_parser.duckdb import BundleExportError, attach_okf
from okf_parser.service import export_duckdb

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
    assert connection.sql("SELECT count(*) FROM okf.links").fetchone() == (1,)
    assert connection.sql("SELECT count(*) FROM okf.diagnostics").fetchone() == (0,)


def test_attach_okf_rejects_unsafe_schema_name(tmp_path: Path) -> None:
    connection = duckdb.connect()

    with pytest.raises(ValueError, match="invalid DuckDB schema"):
        attach_okf(connection, tmp_path, schema='okf"; DROP TABLE x; --')


def test_attach_okf_refuses_to_clobber_existing_tables(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")
    connection = duckdb.connect()
    attach_okf(connection, tmp_path)

    with pytest.raises(BundleExportError) as excinfo:
        attach_okf(connection, tmp_path)

    assert excinfo.value.schema_name == "okf"
    assert "concepts" in excinfo.value.tables
    assert connection.sql("SELECT count(*) FROM okf.concepts").fetchone() == (1,)


def test_attach_okf_overwrite_replaces_existing_tables(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")
    connection = duckdb.connect()
    attach_okf(connection, tmp_path)

    result = attach_okf(connection, tmp_path, overwrite=True)

    assert result["concept_count"] == 1
    assert connection.sql("SELECT count(*) FROM okf.concepts").fetchone() == (1,)


def _write_declared_bundle(tmp_path: Path) -> str:
    (tmp_path / "a.md").write_text(
        "---\ntype: Rotina\ncusto: 12.50\nregistrado_em: 2026-08-07T10:00:00Z\n"
        "codigo: alpha\ntags:\n  - 1\n  - 2\n---\nA\n",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text(
        "---\ntype: Rotina\ncusto: n/a\nregistrado_em: 2026-08-07T11:00:00Z\n"
        "codigo: beta\ntags:\n  - 3\n  - x\n---\nB\n",
        encoding="utf-8",
    )
    types = tmp_path / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        'CREATE TABLE "Rotina" (\n'
        "  custo DECIMAL(18,2),\n"
        "  registrado_em TIMESTAMPTZ,\n"
        "  futuro BIGINT,\n"
        "  tags BIGINT[]\n"
        ");\n"
        "COMMENT ON TABLE \"Rotina\" IS 'Typed routines';\n"
        "COMMENT ON COLUMN \"Rotina\".custo IS 'Exact cost';\n",
        encoding="utf-8",
    )
    return "docs/types/{slug}.md"


def test_attach_okf_materializes_declared_types_per_value(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    connection = duckdb.connect()

    result = attach_okf(connection, tmp_path, spec_template=template)

    assert result["typed_schema"] == "okf_types"
    assert result["typed_tables"] == ["Rotina"]
    rows = connection.execute(
        'SELECT "__okf_raw_custo", custo, futuro, codigo, "__okf_raw_tags", tags '
        'FROM okf_types."Rotina" ORDER BY "__okf_path"'
    ).fetchall()
    assert rows[0] == ("12.50", Decimal("12.50"), None, "alpha", ["1", "2"], [1, 2])
    assert rows[1] == ("n/a", None, None, "beta", ["3", "x"], [3, None])

    described = dict(
        connection.execute('DESCRIBE okf_types."Rotina"').fetchall()[i][:2]
        for i in range(len(connection.execute('DESCRIBE okf_types."Rotina"').fetchall()))
    )
    assert described["custo"] == "DECIMAL(18,2)"
    assert described["registrado_em"] == "TIMESTAMP WITH TIME ZONE"
    assert described["futuro"] == "BIGINT"
    assert described["tags"] == "BIGINT[]"
    assert described["codigo"] == "VARCHAR"

    table_comment = connection.execute(
        "SELECT comment FROM duckdb_tables() WHERE schema_name='okf_types' AND table_name='Rotina'"
    ).fetchone()
    column_comment = connection.execute(
        "SELECT comment FROM duckdb_columns() "
        "WHERE schema_name='okf_types' AND table_name='Rotina' AND column_name='custo'"
    ).fetchone()
    assert table_comment == ("Typed routines",)
    assert column_comment == ("Exact cost",)


def test_attach_okf_uses_catalog_shape_from_ctas_declaration(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Rotina\ncusto: 7.25\n---\n", encoding="utf-8")
    types = tmp_path / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        "CREATE TEMP TABLE staging(custo VARCHAR);\n"
        "INSERT INTO staging VALUES ('1.25');\n"
        'CREATE TABLE "Rotina" AS SELECT TRY_CAST(custo AS DECIMAL(8,2)) AS custo FROM staging;\n',
        encoding="utf-8",
    )
    connection = duckdb.connect()

    attach_okf(connection, tmp_path, spec_template="docs/types/{slug}.md")

    assert connection.execute('SELECT custo FROM okf_types."Rotina"').fetchone() == (
        Decimal("7.25"),
    )
    assert connection.execute(
        "SELECT data_type FROM duckdb_columns() "
        "WHERE schema_name='okf_types' AND table_name='Rotina' AND column_name='custo'"
    ).fetchone() == ("DECIMAL(8,2)",)


def test_typed_table_collision_uses_existing_overwrite_contract(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA okf_types")
    connection.execute('CREATE TABLE okf_types."Rotina" (manual VARCHAR)')

    with pytest.raises(BundleExportError) as excinfo:
        attach_okf(connection, tmp_path, spec_template=template)

    assert excinfo.value.schema_name == "okf_types"
    assert excinfo.value.tables == ("Rotina",)
    description = connection.execute('DESCRIBE okf_types."Rotina"').fetchone()
    assert description is not None
    assert description[0] == "manual"


def test_typed_overwrite_preserves_undeclared_comments(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA okf_types")
    connection.execute('CREATE TABLE okf_types."Rotina" (codigo VARCHAR)')
    connection.execute("COMMENT ON TABLE okf_types.\"Rotina\" IS 'old table comment'")
    connection.execute("COMMENT ON COLUMN okf_types.\"Rotina\".codigo IS 'old code comment'")

    attach_okf(connection, tmp_path, spec_template=template, overwrite=True)

    assert connection.execute(
        "SELECT comment FROM duckdb_columns() "
        "WHERE schema_name='okf_types' AND table_name='Rotina' AND column_name='codigo'"
    ).fetchone() == ("old code comment",)
    # The declaration owns table comments when it supplies one.
    assert connection.execute(
        "SELECT comment FROM duckdb_tables() WHERE schema_name='okf_types' AND table_name='Rotina'"
    ).fetchone() == ("Typed routines",)


def test_typed_materialization_reports_unrecognized_tables_without_dropping(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA okf_types")
    connection.execute("CREATE TABLE okf_types.Legacy (id INTEGER)")

    result = attach_okf(connection, tmp_path, spec_template=template)

    assert result["unrecognized_type_tables"] == ["Legacy"]
    assert connection.execute("SELECT count(*) FROM okf_types.Legacy").fetchone() == (0,)
    assert connection.execute('SELECT count(*) FROM okf_types."Rotina"').fetchone() == (2,)


def test_export_duckdb_reopens_with_typed_tables(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    database = tmp_path / "knowledge.duckdb"

    result = export_duckdb(
        str(tmp_path),
        str(database),
        spec_template=template,
    )

    assert result["typed_table_count"] == 1
    reopened = duckdb.connect(database)
    try:
        assert reopened.execute(
            'SELECT count(*) FROM okf_types."Rotina" WHERE custo IS NOT NULL'
        ).fetchone() == (1,)
    finally:
        reopened.close()


def _snapshot_database(database: Path) -> dict[str, object]:
    """Read every table materialized from a bundle into a comparable, ordered snapshot."""
    connection = duckdb.connect(database, read_only=True)
    try:
        return {
            "concepts": connection.sql("SELECT * FROM okf.concepts ORDER BY path").fetchall(),
            "links": connection.sql(
                "SELECT * FROM okf.links ORDER BY source_id, raw_target"
            ).fetchall(),
            "reserved": connection.sql("SELECT * FROM okf.reserved ORDER BY path").fetchall(),
            "diagnostics": connection.sql(
                "SELECT * FROM okf.diagnostics ORDER BY code, path"
            ).fetchall(),
            "rotina": connection.execute(
                'SELECT * EXCLUDE (registrado_em) FROM okf_types."Rotina" ORDER BY "__okf_path"'
            ).fetchall(),
        }
    finally:
        connection.close()


def test_rebuild_after_deleting_duckdb_matches_original_projection(tmp_path: Path) -> None:
    """Deleting the derived DuckDB file and re-exporting must reproduce it exactly.

    The `.md` bundle is the only source of truth; the `.duckdb` file is a
    disposable projection that must be fully reconstructible from it.
    """
    template = _write_declared_bundle(tmp_path)
    source_files = sorted((tmp_path / "a.md", tmp_path / "b.md"))
    original_source_bytes = {path: path.read_bytes() for path in source_files}
    database = tmp_path / "knowledge.duckdb"

    first_result = export_duckdb(str(tmp_path), str(database), spec_template=template)
    assert database.exists()
    original_snapshot = _snapshot_database(database)

    database.unlink()
    assert not database.exists()

    second_result = export_duckdb(str(tmp_path), str(database), spec_template=template)
    rebuilt_snapshot = _snapshot_database(database)

    assert rebuilt_snapshot == original_snapshot
    assert second_result["concept_count"] == first_result["concept_count"]
    assert second_result["typed_table_count"] == first_result["typed_table_count"]
    for path in source_files:
        assert path.read_bytes() == original_source_bytes[path]


def test_persistent_timestamptz_is_utc_materialized_and_session_stable(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        "---\ntype: Rotina\ninstante: 2026-08-07 10:00:00\n---\nA\n",
        encoding="utf-8",
    )
    types = tmp_path / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        'CREATE TABLE "Rotina" (instante TIMESTAMPTZ);\n',
        encoding="utf-8",
    )
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'America/New_York'")

    attach_okf(connection, tmp_path, spec_template="docs/types/{slug}.md")

    assert connection.execute("SELECT current_setting('TimeZone')").fetchone() == (
        "America/New_York",
    )
    expected = connection.execute(
        "SELECT epoch_us(TIMESTAMPTZ '2026-08-07 10:00:00+00')"
    ).fetchone()
    first = connection.execute('SELECT epoch_us(instante) FROM okf_types."Rotina"').fetchone()
    connection.execute("SET TimeZone = 'Asia/Tokyo'")
    second = connection.execute('SELECT epoch_us(instante) FROM okf_types."Rotina"').fetchone()
    assert expected is not None
    assert first == expected
    assert second == expected
