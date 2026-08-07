"""Regression tests for declared-schema correctness after RFC 0006 merged."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.declared_schema import parse_declared_schema
from okf_parser.schema_export import SchemaExportError, export_json_schema

if TYPE_CHECKING:
    from pathlib import Path

TEMPLATE = "docs/types/{slug}.md"


def _write_concept(path: Path, frontmatter: str) -> None:
    path.write_text(f"---\n{frontmatter}---\nBody\n", encoding="utf-8")


def test_declared_table_identity_follows_duckdb_identifier_case_rules() -> None:
    schema = parse_declared_schema('CREATE TABLE "Rotina" (id BIGINT);', "rotina")

    assert schema.table_name == "Rotina"
    assert {name: logical.sql for name, logical in schema.columns.items()} == {"id": "BIGINT"}


def test_declared_columns_are_read_from_the_non_temporary_table_identity() -> None:
    sql = """
    CREATE TABLE "Rotina" (permanent_col BIGINT);
    CREATE TEMP TABLE "Rotina" (temporary_col VARCHAR);
    """

    schema = parse_declared_schema(sql, "Rotina")

    assert {name: logical.sql for name, logical in schema.columns.items()} == {
        "permanent_col": "BIGINT"
    }


def test_declared_but_unobserved_column_is_exported_as_optional(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: Rotina\nnome: exemplo\n")
    declared = tmp_path / "docs" / "types" / "rotina.schema.sql"
    declared.parent.mkdir(parents=True)
    declared.write_text('CREATE TABLE "Rotina" (nome VARCHAR, futuro BIGINT);', encoding="utf-8")

    schema = export_json_schema(str(tmp_path), spec_template=TEMPLATE)["schemas"]["Rotina"]

    assert schema["properties"]["futuro"]["type"] == "integer"
    assert "futuro" not in schema["required"]


def test_declared_scalar_intent_wins_over_divergent_observed_shape(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: Rotina\nvalues: [1, 2]\n")
    declared = tmp_path / "docs" / "types" / "rotina.schema.sql"
    declared.parent.mkdir(parents=True)
    declared.write_text('CREATE TABLE "Rotina" (values BIGINT);', encoding="utf-8")

    schema = export_json_schema(str(tmp_path), spec_template=TEMPLATE)["schemas"]["Rotina"]

    assert schema["properties"]["values"]["type"] == "integer"
    assert schema["properties"]["values"]["x-okf-duckdb-type"] == "BIGINT"


def test_existing_derived_path_collision_is_an_explicit_export_error(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: Revisao Ciencia\nvalor: '1'\n")
    _write_concept(tmp_path / "two.md", "type: Revisão Ciência\nvalor: '2'\n")
    declared = tmp_path / "docs" / "types" / "revisao-ciencia.schema.sql"
    declared.parent.mkdir(parents=True)
    declared.write_text('CREATE TABLE "Revisao Ciencia" (valor BIGINT);', encoding="utf-8")

    with pytest.raises(SchemaExportError, match="declared schema path collision"):
        export_json_schema(str(tmp_path), spec_template=TEMPLATE)


def test_existing_malformed_declaration_is_not_silently_ignored(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: Rotina\nvalor: '1'\n")
    declared = tmp_path / "docs" / "types" / "rotina.schema.sql"
    declared.parent.mkdir(parents=True)
    declared.write_text("not valid sql (((", encoding="utf-8")

    with pytest.raises(SchemaExportError, match="invalid declared schema"):
        export_json_schema(str(tmp_path), spec_template=TEMPLATE)
