"""Integration tests for the DuckDB export (``okf-db/src/export.rs``).

The binary writes the database; the Python ``duckdb`` package only reads it
back, which also proves the file is an ordinary DuckDB database.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import duckdb
import pytest

from okf_parser.service import BundleExportError, export_duckdb

if TYPE_CHECKING:
    from pathlib import Path


def _export(
    bundle: Path,
    database: Path,
    *,
    schema: str = "okf",
    overwrite: bool = False,
    spec_template: str | None = None,
) -> dict[str, object]:
    return dict(
        export_duckdb(
            str(bundle), str(database), schema, overwrite=overwrite, spec_template=spec_template
        )
    )


def _query(database: Path, sql: str) -> list[tuple[object, ...]]:
    with duckdb.connect(database, read_only=True) as connection:
        return connection.execute(sql).fetchall()


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    return bundle


def test_export_materializes_queryable_tables(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "a.md").write_text(
        "---\ntype: Node\nrelated: /b.md\n---\n[B](b.md)\n",
        encoding="utf-8",
    )
    (bundle / "b.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")
    database = tmp_path / "knowledge.duckdb"

    result = _export(bundle, database)

    assert result["conformant"]
    assert result["database"] == str(database)
    assert _query(database, "SELECT count(*) FROM okf.concepts") == [(2,)]
    assert _query(database, "SELECT count(*) FROM okf.links") == [(1,)]
    assert _query(database, "SELECT count(*) FROM okf.diagnostics") == [(0,)]


def test_export_rejects_unsafe_schema_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid DuckDB schema"):
        _export(_bundle(tmp_path), tmp_path / "knowledge.duckdb", schema='okf"; DROP TABLE x; --')


def test_export_refuses_to_clobber_existing_tables(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "a.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")
    database = tmp_path / "knowledge.duckdb"
    _export(bundle, database)

    with pytest.raises(BundleExportError, match="--overwrite") as excinfo:
        _export(bundle, database)

    assert excinfo.value.schema_name == "okf"
    assert "concepts" in excinfo.value.tables
    assert _query(database, "SELECT count(*) FROM okf.concepts") == [(1,)]


def test_export_overwrite_replaces_existing_tables(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "a.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")
    database = tmp_path / "knowledge.duckdb"
    _export(bundle, database)

    result = _export(bundle, database, overwrite=True)

    assert result["concept_count"] == 1
    assert _query(database, "SELECT count(*) FROM okf.concepts") == [(1,)]


def _write_declared_bundle(bundle: Path) -> str:
    (bundle / "a.md").write_text(
        "---\ntype: Rotina\ncusto: 12.50\nregistrado_em: 2026-08-07T10:00:00Z\n"
        "codigo: alpha\ntags:\n  - 1\n  - 2\n---\nA\n",
        encoding="utf-8",
    )
    (bundle / "b.md").write_text(
        "---\ntype: Rotina\ncusto: n/a\nregistrado_em: 2026-08-07T11:00:00Z\n"
        "codigo: beta\ntags:\n  - 3\n  - x\n---\nB\n",
        encoding="utf-8",
    )
    types = bundle / "docs" / "types"
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


def test_export_materializes_declared_types_per_value(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    template = _write_declared_bundle(bundle)
    database = tmp_path / "knowledge.duckdb"

    result = _export(bundle, database, spec_template=template)

    assert result["typed_schema"] == "okf_types"
    assert result["typed_tables"] == ["Rotina"]
    rows = _query(
        database,
        'SELECT "__okf_raw_custo", custo, futuro, codigo, "__okf_raw_tags", tags, '
        '"__okf_body_lines" FROM okf_types."Rotina" ORDER BY "__okf_path"',
    )
    assert rows[0] == ("12.50", Decimal("12.50"), None, "alpha", ["1", "2"], [1, 2], ["A"])
    assert rows[1] == ("n/a", None, None, "beta", ["3", "x"], [3, None], ["B"])

    described = {row[0]: row[1] for row in _query(database, 'DESCRIBE okf_types."Rotina"')}
    assert described["custo"] == "DECIMAL(18,2)"
    assert described["registrado_em"] == "TIMESTAMP WITH TIME ZONE"
    assert described["futuro"] == "BIGINT"
    assert described["tags"] == "BIGINT[]"
    assert described["codigo"] == "VARCHAR"

    assert _query(
        database,
        "SELECT comment FROM duckdb_tables() WHERE schema_name='okf_types' AND table_name='Rotina'",
    ) == [("Typed routines",)]
    assert _query(
        database,
        "SELECT comment FROM duckdb_columns() "
        "WHERE schema_name='okf_types' AND table_name='Rotina' AND column_name='custo'",
    ) == [("Exact cost",)]


def test_export_uses_catalog_shape_from_ctas_declaration(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "a.md").write_text("---\ntype: Rotina\ncusto: 7.25\n---\n", encoding="utf-8")
    types = bundle / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        "CREATE TEMP TABLE staging(custo VARCHAR);\n"
        "INSERT INTO staging VALUES ('1.25');\n"
        'CREATE TABLE "Rotina" AS SELECT TRY_CAST(custo AS DECIMAL(8,2)) AS custo FROM staging;\n',
        encoding="utf-8",
    )
    database = tmp_path / "knowledge.duckdb"

    _export(bundle, database, spec_template="docs/types/{slug}.md")

    assert _query(database, 'SELECT custo FROM okf_types."Rotina"') == [(Decimal("7.25"),)]
    assert _query(
        database,
        "SELECT data_type FROM duckdb_columns() "
        "WHERE schema_name='okf_types' AND table_name='Rotina' AND column_name='custo'",
    ) == [("DECIMAL(8,2)",)]


def _prepare(database: Path, *statements: str) -> None:
    with duckdb.connect(database) as connection:
        for statement in statements:
            connection.execute(statement)


def test_typed_table_collision_uses_existing_overwrite_contract(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    template = _write_declared_bundle(bundle)
    database = tmp_path / "knowledge.duckdb"
    _prepare(
        database, "CREATE SCHEMA okf_types", 'CREATE TABLE okf_types."Rotina" (manual VARCHAR)'
    )

    with pytest.raises(BundleExportError) as excinfo:
        _export(bundle, database, spec_template=template)

    assert excinfo.value.schema_name == "okf_types"
    assert excinfo.value.tables == ("Rotina",)
    assert _query(database, 'DESCRIBE okf_types."Rotina"')[0][0] == "manual"
    # The refused export left nothing behind, the base tables included.
    assert _query(
        database, "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'okf'"
    ) == [(0,)]


def test_typed_overwrite_preserves_undeclared_comments(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    template = _write_declared_bundle(bundle)
    database = tmp_path / "knowledge.duckdb"
    _prepare(
        database,
        "CREATE SCHEMA okf_types",
        'CREATE TABLE okf_types."Rotina" (codigo VARCHAR)',
        "COMMENT ON TABLE okf_types.\"Rotina\" IS 'old table comment'",
        "COMMENT ON COLUMN okf_types.\"Rotina\".codigo IS 'old code comment'",
    )

    _export(bundle, database, spec_template=template, overwrite=True)

    assert _query(
        database,
        "SELECT comment FROM duckdb_columns() "
        "WHERE schema_name='okf_types' AND table_name='Rotina' AND column_name='codigo'",
    ) == [("old code comment",)]
    # The declaration owns table comments when it supplies one.
    assert _query(
        database,
        "SELECT comment FROM duckdb_tables() WHERE schema_name='okf_types' AND table_name='Rotina'",
    ) == [("Typed routines",)]


def test_typed_materialization_reports_unrecognized_tables_without_dropping(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    template = _write_declared_bundle(bundle)
    database = tmp_path / "knowledge.duckdb"
    _prepare(database, "CREATE SCHEMA okf_types", "CREATE TABLE okf_types.Legacy (id INTEGER)")

    result = _export(bundle, database, spec_template=template)

    assert result["unrecognized_type_tables"] == ["Legacy"]
    assert _query(database, "SELECT count(*) FROM okf_types.Legacy") == [(0,)]
    assert _query(database, 'SELECT count(*) FROM okf_types."Rotina"') == [(2,)]


def test_export_reopens_with_typed_tables(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    template = _write_declared_bundle(bundle)
    database = tmp_path / "knowledge.duckdb"

    result = _export(bundle, database, spec_template=template)

    assert result["typed_table_count"] == 1
    assert _query(database, 'SELECT count(*) FROM okf_types."Rotina" WHERE custo IS NOT NULL') == [
        (1,)
    ]


def _snapshot_database(database: Path) -> dict[str, object]:
    """Read every table materialized from a bundle into a comparable, ordered snapshot."""
    return {
        "concepts": _query(database, "SELECT * FROM okf.concepts ORDER BY path"),
        "links": _query(database, "SELECT * FROM okf.links ORDER BY source_id, raw_target"),
        "reserved": _query(database, "SELECT * FROM okf.reserved ORDER BY path"),
        "diagnostics": _query(database, "SELECT * FROM okf.diagnostics ORDER BY code, path"),
        "rotina": _query(
            database,
            'SELECT * EXCLUDE (registrado_em) FROM okf_types."Rotina" ORDER BY "__okf_path"',
        ),
    }


def test_rebuild_after_deleting_duckdb_matches_original_projection(tmp_path: Path) -> None:
    """Deleting the derived DuckDB file and re-exporting must reproduce it exactly.

    The `.md` bundle is the only source of truth; the `.duckdb` file is a
    disposable projection that must be fully reconstructible from it.
    """
    bundle = _bundle(tmp_path)
    template = _write_declared_bundle(bundle)
    source_files = sorted((bundle / "a.md", bundle / "b.md"))
    original_source_bytes = {path: path.read_bytes() for path in source_files}
    database = tmp_path / "knowledge.duckdb"

    first_result = _export(bundle, database, spec_template=template)
    original_snapshot = _snapshot_database(database)

    database.unlink()

    second_result = _export(bundle, database, spec_template=template)

    assert _snapshot_database(database) == original_snapshot
    assert second_result["concept_count"] == first_result["concept_count"]
    assert second_result["typed_table_count"] == first_result["typed_table_count"]
    for path in source_files:
        assert path.read_bytes() == original_source_bytes[path]


def test_timestamptz_is_materialized_as_utc_and_session_stable(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "a.md").write_text(
        "---\ntype: Rotina\ninstante: 2026-08-07 10:00:00\n---\nA\n",
        encoding="utf-8",
    )
    types = bundle / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        'CREATE TABLE "Rotina" (instante TIMESTAMPTZ);\n',
        encoding="utf-8",
    )
    database = tmp_path / "knowledge.duckdb"

    _export(bundle, database, spec_template="docs/types/{slug}.md")

    with duckdb.connect(database, read_only=True) as connection:
        expected = connection.execute(
            "SELECT epoch_us(TIMESTAMPTZ '2026-08-07 10:00:00+00')"
        ).fetchone()
        connection.execute("SET TimeZone = 'America/New_York'")
        first = connection.execute('SELECT epoch_us(instante) FROM okf_types."Rotina"').fetchone()
        connection.execute("SET TimeZone = 'Asia/Tokyo'")
        second = connection.execute('SELECT epoch_us(instante) FROM okf_types."Rotina"').fetchone()
    assert expected is not None
    assert first == expected
    assert second == expected
