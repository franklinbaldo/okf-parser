"""Tests for RFC 0007 bundle-level relational identity."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from okf_parser import bundle as bundle_module
from okf_parser import rust_core
from okf_parser.bundle import validate_path
from okf_parser.relational_schema import RelationalSchemaError, parse_relational_schema

if TYPE_CHECKING:
    from pydantic import BaseModel

    from okf_parser.models import Violation
    from okf_parser.rust_core import NativeResponse

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


def validate_relations(root: Path, schema_path: Path) -> list[Violation]:
    """The relational diagnostics ``check --relational-schema`` adds."""
    report = validate_path(root, relational_schema=schema_path)
    return [item for item in report.violations if item.code in {"OKF020", "OKF021", "OKF022"}]


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

    assert validate_relations(tmp_path, schema_path) == []


def test_validate_relations_reports_duplicate_unique_key(tmp_path: Path) -> None:
    _write(tmp_path / "regra-1.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "regra-2.md", _concept("Regra", nome="regra-a"))
    schema_path = tmp_path / "okf.schema.sql"
    _write(schema_path, RELATIONAL_SQL)

    diagnostics = validate_relations(tmp_path, schema_path)

    [duplicate] = [item for item in diagnostics if item.code == "OKF021"]
    assert duplicate.path == "regra-2.md"
    assert "regra-a" in duplicate.message
    assert "regra-1.md" in duplicate.message


def test_validate_relations_reports_dangling_foreign_key(tmp_path: Path) -> None:
    _write(tmp_path / "regra.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1", regra="regra-ausente"))
    schema_path = tmp_path / "okf.schema.sql"
    _write(schema_path, RELATIONAL_SQL)

    diagnostics = validate_relations(tmp_path, schema_path)

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

    assert validate_relations(tmp_path, schema_path) == []


def test_validate_path_applies_explicit_relational_schema(tmp_path: Path) -> None:
    _write(tmp_path / "regra.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1", regra="ausente"))
    _write(tmp_path / "okf.schema.sql", RELATIONAL_SQL)

    report = validate_path(tmp_path, relational_schema=Path("okf.schema.sql"))

    assert not report.is_conformant
    assert [item.code for item in report.violations] == ["OKF022"]


def test_relational_validation_reads_the_bundle_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One `__check` answers the relational diagnostics too, from the same
    # read as the rest of the report.
    _write(tmp_path / "regra.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1", regra="ausente"))
    _write(tmp_path / "okf.schema.sql", RELATIONAL_SQL)
    commands: list[str] = []
    real = rust_core.call_native

    def spy(command: str, request: BaseModel) -> NativeResponse:
        commands.append(command)
        return real(command, request)

    def forbidden(*_: object, **__: object) -> None:
        pytest.fail("the relational path must not load the bundle a second time")

    monkeypatch.setattr(rust_core, "call_native", spy)
    monkeypatch.setattr(bundle_module, "rust_load_bundle", forbidden)

    report = validate_path(tmp_path, relational_schema=Path("okf.schema.sql"))

    assert commands == ["__check"]
    assert [item.code for item in report.violations] == ["OKF022"]


def test_relational_messages_name_the_type_the_key_and_the_first_owner(tmp_path: Path) -> None:
    _write(tmp_path / "regra-1.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "regra-2.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", regra="ausente"))
    schema_path = tmp_path / "okf.schema.sql"
    _write(schema_path, RELATIONAL_SQL)

    messages = {
        (item.code, item.path): item.message for item in validate_relations(tmp_path, schema_path)
    }

    assert messages["OKF021", "regra-2.md"].startswith(
        '`Regra` nome = "regra-a" is already used by regra-1.md'
    )
    assert messages["OKF021", "fund.md"].startswith("`Fundamentacao` has no id but primary key")
    assert messages["OKF022", "fund.md"].startswith(
        '`Fundamentacao` regra = "ausente" matches no `Regra` nome'
    )


def test_a_failing_relational_schema_is_its_own_error(tmp_path: Path) -> None:
    _write(tmp_path / "okf.schema.sql", "CREATE TABLE (")

    with pytest.raises(RelationalSchemaError, match="relational schema script failed"):
        validate_path(tmp_path, relational_schema=Path("okf.schema.sql"))
