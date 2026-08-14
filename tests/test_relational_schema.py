"""Tests for RFC 0007 bundle-level relational identity."""

from __future__ import annotations

from pathlib import Path

from okf_parser.bundle import load_bundle, validate_path
from okf_parser.relational_schema import parse_relational_schema, validate_relations

RELATIONAL_SQL = """
CREATE TABLE "Regra" (
    nome VARCHAR UNIQUE
);
CREATE TABLE "Fundamentacao" (
    id VARCHAR PRIMARY KEY,
    regra VARCHAR REFERENCES "Regra"(nome)
);
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _concept(concept_type: str, **fields: str) -> str:
    lines = ["---", f"type: {concept_type}"]
    lines.extend(f"{name}: {value}" for name, value in fields.items())
    lines.extend(["---", ""])
    return "\n".join(lines)


def test_parse_relational_schema_reads_unique_primary_and_foreign_keys() -> None:
    schema = parse_relational_schema(RELATIONAL_SQL)

    assert {(item.table, item.columns, item.primary) for item in schema.keys} == {
        ("Regra", ("nome",), False),
        ("Fundamentacao", ("id",), True),
    }
    [foreign_key] = schema.foreign_keys
    assert foreign_key.table == "Fundamentacao"
    assert foreign_key.columns == ("regra",)
    assert foreign_key.referenced_table == "Regra"
    assert foreign_key.referenced_columns == ("nome",)


def test_validate_relations_accepts_one_to_many_relationship(tmp_path: Path) -> None:
    _write(tmp_path / "regra.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund-1.md", _concept("Fundamentacao", id="f1", regra="regra-a"))
    _write(tmp_path / "fund-2.md", _concept("Fundamentacao", id="f2", regra="regra-a"))
    schema_path = tmp_path / "okf.schema.sql"
    _write(schema_path, RELATIONAL_SQL)

    assert validate_relations(load_bundle(tmp_path), schema_path) == []


def test_validate_relations_reports_duplicate_unique_key(tmp_path: Path) -> None:
    _write(tmp_path / "regra-1.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "regra-2.md", _concept("Regra", nome="regra-a"))
    schema_path = tmp_path / "okf.schema.sql"
    _write(schema_path, RELATIONAL_SQL)

    diagnostics = validate_relations(load_bundle(tmp_path), schema_path)

    [duplicate] = [item for item in diagnostics if item.code == "OKF021"]
    assert duplicate.path == "regra-2.md"
    assert "regra-a" in duplicate.message
    assert "regra-1.md" in duplicate.message


def test_validate_relations_reports_dangling_foreign_key(tmp_path: Path) -> None:
    _write(tmp_path / "regra.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1", regra="regra-ausente"))
    schema_path = tmp_path / "okf.schema.sql"
    _write(schema_path, RELATIONAL_SQL)

    diagnostics = validate_relations(load_bundle(tmp_path), schema_path)

    [dangling] = [item for item in diagnostics if item.code == "OKF022"]
    assert dangling.path == "fund.md"
    assert "regra-ausente" in dangling.message
    assert "Regra" in dangling.message


def test_unique_and_foreign_key_allow_absent_nullable_values(tmp_path: Path) -> None:
    _write(tmp_path / "regra-1.md", _concept("Regra"))
    _write(tmp_path / "regra-2.md", _concept("Regra"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1"))
    schema_path = tmp_path / "okf.schema.sql"
    _write(schema_path, RELATIONAL_SQL)

    assert validate_relations(load_bundle(tmp_path), schema_path) == []


def test_validate_path_applies_explicit_relational_schema(tmp_path: Path) -> None:
    _write(tmp_path / "regra.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1", regra="ausente"))
    _write(tmp_path / "okf.schema.sql", RELATIONAL_SQL)

    report = validate_path(tmp_path, relational_schema=Path("okf.schema.sql"))

    assert not report.is_conformant
    assert [item.code for item in report.violations] == ["OKF022"]
