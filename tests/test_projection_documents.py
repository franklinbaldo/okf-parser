"""Tests for RFC 0018 step 4: `type: Projection` documents and their resolution.

Projection documents are read by the binary (``okf-db/src/schema/relations.rs``);
each test writes a bundle and reads back the contracts it compiles to.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from okf_parser.schema_contract import (
    FieldContract,
    ListNode,
    ProjectionError,
    RefNode,
    TypeContract,
)
from okf_parser.schema_export import build_schema_contracts

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE "Processo" (
    cnj VARCHAR PRIMARY KEY,
    apenso_de VARCHAR REFERENCES "Processo"(cnj)
);
CREATE TABLE "Publicacao" (
    fonte VARCHAR,
    source_id VARCHAR,
    processo VARCHAR REFERENCES "Processo"(cnj),
    PRIMARY KEY (fonte, source_id)
);
CREATE TABLE "EventoProcessual" (
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


def _document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "type": "Projection",
        "name": "ProcessoConsultar",
        "root": "Processo",
        "include": [{"relation": "Publicacao.processo", "as": "publicacoes"}],
    }
    document.update(overrides)
    return document


def _frontmatter(document: dict[str, object]) -> str:
    # JSON values are YAML flow values, so each key is one line.
    lines = [f"{key}: {json.dumps(value)}" for key, value in document.items()]
    return "---\n" + "\n".join(lines) + "\n---\n"


def _bundle(root: Path, *projections: dict[str, object]) -> Path:
    _write(root / "okf.schema.sql", SCHEMA_SQL)
    _write(root / "processo.md", "---\ntype: Processo\ncnj: '1'\napenso_de: '1'\n---\n")
    _write(
        root / "publicacao.md",
        "---\ntype: Publicacao\nfonte: djen\nsource_id: a\nprocesso: '1'\n---\n",
    )
    _write(
        root / "evento.md",
        "---\ntype: EventoProcessual\nid: e1\npublicacao_fonte: djen\n"
        "publicacao_source_id: a\n---\n",
    )
    for index, projection in enumerate(projections):
        _write(root / f"projection-{index}.md", _frontmatter(projection))
    return root


def _contracts(root: Path, *projections: dict[str, object]) -> tuple[TypeContract, ...]:
    _bundle(root, *projections)
    return build_schema_contracts(str(root), relational_schema="okf.schema.sql")


def _projection(root: Path, document: dict[str, object]) -> TypeContract:
    contracts = _contracts(root, document)
    return next(contract for contract in contracts if contract.concept_type == document["name"])


def _members(contract: TypeContract) -> list[FieldContract]:
    return [field for field in contract.root.fields if _reference(field) is not None]


def _reference(field: FieldContract) -> RefNode | None:
    value = field.value.item if isinstance(field.value, ListNode) else field.value
    return value if isinstance(value, RefNode) and value.embedded else None


def test_relation_pointing_at_the_root_is_a_collection(tmp_path: Path) -> None:
    contract = _projection(tmp_path, _document())
    assert contract.model_name == "ProcessoConsultarProjection"
    [member] = _members(contract)
    assert member.name == "publicacoes"
    assert isinstance(member.value, ListNode)
    reference = _reference(member)
    assert reference is not None
    assert reference.concept_type == "Publicacao"
    assert reference.columns == ("processo",)
    assert member.nullable is False


def test_relation_on_the_root_is_a_single_value(tmp_path: Path) -> None:
    document = _document(
        name="ProcessoApenso",
        include=[{"relation": "Processo.apenso_de", "as": "apenso"}],
    )
    [member] = _members(_projection(tmp_path, document))
    assert isinstance(member.value, RefNode)
    assert member.value.concept_type == "Processo"


def test_optional_member_is_nullable(tmp_path: Path) -> None:
    document = _document(
        include=[{"relation": "Publicacao.processo", "as": "publicacoes", "optional": True}],
    )
    [member] = _members(_projection(tmp_path, document))
    assert member.nullable is True


def test_composite_relation_resolves_by_any_participating_column(tmp_path: Path) -> None:
    document = _document(
        name="PublicacaoConsultar",
        root="Publicacao",
        include=[{"relation": "EventoProcessual.publicacao_source_id", "as": "eventos"}],
    )
    [member] = _members(_projection(tmp_path, document))
    assert isinstance(member.value, ListNode)
    reference = _reference(member)
    assert reference is not None
    assert reference.columns == ("publicacao_fonte", "publicacao_source_id")


def test_members_follow_the_root_fields_in_declared_order(tmp_path: Path) -> None:
    document = _document(
        include=[
            {"relation": "Processo.apenso_de", "as": "apenso"},
            {"relation": "Publicacao.processo", "as": "publicacoes"},
        ],
    )
    contract = _projection(tmp_path, document)
    assert [field.name for field in contract.root.fields][-2:] == ["apenso", "publicacoes"]


def test_projection_without_members_is_its_root(tmp_path: Path) -> None:
    contracts = _contracts(tmp_path, _document(include=[]))
    by_type = {contract.concept_type: contract for contract in contracts}
    assert by_type["ProcessoConsultar"].root == by_type["Processo"].root


def test_projections_follow_the_concept_types_ordered_by_name(tmp_path: Path) -> None:
    contracts = _contracts(tmp_path, _document(name="Zeta"), _document(name="Alpha"))
    assert [contract.concept_type for contract in contracts][-2:] == ["Alpha", "Zeta"]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"name": ""}, "projection document has no name"),
        ({"root": ""}, "has no root"),
        ({"root": "Fantasma"}, "unknown concept type"),
        ({"name": "Processo"}, "collides with concept type"),
        ({"include": {"relation": "x"}}, "include must be a list"),
        ({"include": ["Publicacao.processo"]}, "include member must be a mapping"),
        ({"include": [{"as": "publicacoes"}]}, "member has no relation"),
        ({"include": [{"relation": "Publicacao.processo"}]}, "has no 'as' name"),
        ({"include": [{"relation": "Publicacao", "as": "p"}]}, "must be written"),
        ({"include": [{"relation": "Publicacao.inexistente", "as": "p"}]}, "does not declare"),
        ({"include": [{"relation": "EventoProcessual.id", "as": "e"}]}, "does not declare"),
        (
            {
                "include": [
                    {"relation": "EventoProcessual.publicacao_fonte", "as": "eventos"},
                ],
            },
            "connects Publicacao to EventoProcessual, not Processo",
        ),
        (
            {
                "include": [
                    {"relation": "Publicacao.processo", "as": "x"},
                    {"relation": "Processo.apenso_de", "as": "x"},
                ],
            },
            "declares 'x' twice",
        ),
        (
            {"include": [{"relation": "Publicacao.processo", "as": "p", "limit": 10}]},
            "unrecognized member key",
        ),
        (
            {"include": [{"relation": "Publicacao.processo", "as": "p", "optional": "yes"}]},
            "optional must be a boolean",
        ),
    ],
)
def test_normative_errors(tmp_path: Path, overrides: dict[str, object], expected: str) -> None:
    with pytest.raises(ProjectionError, match=expected):
        _contracts(tmp_path, _document(**overrides))


def test_duplicate_projection_names_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ProjectionError, match="declared twice"):
        _contracts(tmp_path, _document(), _document())


def test_projections_need_a_relational_schema(tmp_path: Path) -> None:
    _bundle(tmp_path, _document())
    with pytest.raises(ProjectionError, match="relational schema"):
        build_schema_contracts(str(tmp_path))


def test_projection_documents_export_by_authored_name_not_marker_type(tmp_path: Path) -> None:
    contracts = _contracts(tmp_path, _document())
    contract_types = {contract.concept_type for contract in contracts}
    assert "Projection" not in contract_types
    assert contract_types == {"EventoProcessual", "Processo", "Publicacao", "ProcessoConsultar"}
