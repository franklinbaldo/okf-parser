"""Tests for RFC 0018 referential schema export (`--refs=key`)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from okf_parser.schema_contract import (
    FieldContract,
    RefNode,
    TypeContract,
    node_json_schema,
    node_zod,
)
from okf_parser.schema_export import (
    SchemaExportError,
    build_schema_contracts,
    export_json_schema,
    export_pydantic_source,
    export_zod_schema,
)
from okf_parser.service import schema_bundle

if TYPE_CHECKING:
    from pathlib import Path

SINGLE_COLUMN_SQL = """
CREATE TABLE "Regra" (
    nome VARCHAR UNIQUE
);
CREATE TABLE "Fundamentacao" (
    id VARCHAR PRIMARY KEY,
    regra VARCHAR REFERENCES "Regra"(nome)
);
"""

COMPOSITE_SQL = """
CREATE TABLE "Publicacao" (
    fonte VARCHAR,
    source_id VARCHAR,
    PRIMARY KEY (fonte, source_id)
);
CREATE TABLE "Evento" (
    id VARCHAR PRIMARY KEY,
    publicacao_fonte VARCHAR,
    publicacao_source_id VARCHAR,
    FOREIGN KEY (publicacao_fonte, publicacao_source_id)
        REFERENCES "Publicacao"(fonte, source_id)
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


@pytest.fixture
def single_column_bundle(tmp_path: Path) -> Path:
    _write(tmp_path / "regra.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1", regra="regra-a"))
    _write(tmp_path / "okf.schema.sql", SINGLE_COLUMN_SQL)
    return tmp_path


@pytest.fixture
def composite_bundle(tmp_path: Path) -> Path:
    _write(tmp_path / "pub.md", _concept("Publicacao", fonte="djen", source_id="123"))
    _write(
        tmp_path / "evento.md",
        _concept("Evento", id="e1", publicacao_fonte="djen", publicacao_source_id="123"),
    )
    _write(tmp_path / "okf.schema.sql", COMPOSITE_SQL)
    return tmp_path


def _field(contracts: tuple[TypeContract, ...], concept_type: str, name: str) -> FieldContract:
    [contract] = [item for item in contracts if item.concept_type == concept_type]
    [field] = [item for item in contract.root.fields if item.name == name]
    return field


def test_declared_foreign_key_compiles_to_a_reference_node(single_column_bundle: Path) -> None:
    contracts = build_schema_contracts(
        str(single_column_bundle),
        relational_schema=str(single_column_bundle / "okf.schema.sql"),
    )

    reference = _field(contracts, "Fundamentacao", "regra").value
    assert isinstance(reference, RefNode)
    assert reference.concept_type == "Regra"
    assert reference.columns == ("regra",)
    assert reference.referenced_columns == ("nome",)
    assert reference.position == 0


def test_reference_keeps_the_scalar_it_carries(single_column_bundle: Path) -> None:
    contracts = build_schema_contracts(
        str(single_column_bundle),
        relational_schema=str(single_column_bundle / "okf.schema.sql"),
    )
    reference = _field(contracts, "Fundamentacao", "regra").value

    assert node_json_schema(reference)["type"] == "string"
    assert node_zod(reference).startswith("z.string()")


def test_json_schema_carries_the_reference_metadata(single_column_bundle: Path) -> None:
    exported = export_json_schema(
        str(single_column_bundle),
        relational_schema=str(single_column_bundle / "okf.schema.sql"),
    )
    regra = exported["schemas"]["Fundamentacao"]["properties"]["regra"]

    assert regra["x-okf-references"] == {
        "type": "Regra",
        "columns": ["regra"],
        "referencedColumns": ["nome"],
        "position": 0,
    }


def test_zod_describes_the_reference(single_column_bundle: Path) -> None:
    source = export_zod_schema(
        str(single_column_bundle),
        relational_schema=str(single_column_bundle / "okf.schema.sql"),
    )

    assert '.describe("references Regra(nome)")' in source


def test_pydantic_source_carries_the_reference_metadata(single_column_bundle: Path) -> None:
    source = export_pydantic_source(
        str(single_column_bundle),
        relational_schema=str(single_column_bundle / "okf.schema.sql"),
    )

    assert "json_schema_extra=" in source
    assert '"x-okf-references"' in source
    assert '"type": "Regra"' in source


def test_composite_key_marks_every_participating_column(composite_bundle: Path) -> None:
    exported = export_json_schema(
        str(composite_bundle),
        relational_schema=str(composite_bundle / "okf.schema.sql"),
    )
    properties = exported["schemas"]["Evento"]["properties"]

    assert properties["publicacao_fonte"]["x-okf-references"] == {
        "type": "Publicacao",
        "columns": ["publicacao_fonte", "publicacao_source_id"],
        "referencedColumns": ["fonte", "source_id"],
        "position": 0,
    }
    assert properties["publicacao_source_id"]["x-okf-references"]["position"] == 1


def test_export_is_unchanged_without_the_flag(single_column_bundle: Path) -> None:
    plain = export_json_schema(str(single_column_bundle))
    zod = export_zod_schema(str(single_column_bundle))

    assert "x-okf-references" not in json.dumps(plain)
    assert ".describe(" not in zod


def test_reference_to_an_undeclared_column_is_reported(tmp_path: Path) -> None:
    _write(tmp_path / "regra.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1"))
    _write(tmp_path / "okf.schema.sql", SINGLE_COLUMN_SQL)

    with pytest.raises(SchemaExportError, match=r"Fundamentacao\.regra"):
        build_schema_contracts(
            str(tmp_path),
            relational_schema=str(tmp_path / "okf.schema.sql"),
        )


def test_reference_to_a_type_with_no_documents_still_compiles(tmp_path: Path) -> None:
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1", regra="regra-a"))
    _write(tmp_path / "okf.schema.sql", SINGLE_COLUMN_SQL)

    contracts = build_schema_contracts(
        str(tmp_path),
        relational_schema=str(tmp_path / "okf.schema.sql"),
    )

    assert isinstance(_field(contracts, "Fundamentacao", "regra").value, RefNode)


def test_cli_schema_accepts_the_relational_schema_flag(single_column_bundle: Path) -> None:
    payload = schema_bundle(
        str(single_column_bundle),
        "json",
        relational_schema=str(single_column_bundle / "okf.schema.sql"),
    )

    assert isinstance(payload, dict)
    assert "x-okf-references" in json.dumps(payload["schemas"])


def test_relative_schema_path_resolves_against_the_bundle_root(single_column_bundle: Path) -> None:
    contracts = build_schema_contracts(
        str(single_column_bundle), relational_schema="okf.schema.sql"
    )

    assert isinstance(_field(contracts, "Fundamentacao", "regra").value, RefNode)
