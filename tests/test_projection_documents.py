"""Tests for RFC 0018 step 4: `type: Projection` documents and their resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.projections import (
    ProjectionError,
    load_projections,
    parse_projections,
)
from okf_parser.relational_schema import parse_relational_schema
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

CONCEPT_TYPES = ("EventoProcessual", "Processo", "Publicacao")


def _foreign_keys() -> tuple[object, ...]:
    return parse_relational_schema(SCHEMA_SQL).foreign_keys


def _parse(document: dict[str, object]) -> tuple[object, ...]:
    return parse_projections(
        [document],
        parse_relational_schema(SCHEMA_SQL).foreign_keys,
        concept_types=CONCEPT_TYPES,
    )


def _document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "type": "Projection",
        "name": "ProcessoConsultar",
        "root": "Processo",
        "include": [{"relation": "Publicacao.processo", "as": "publicacoes"}],
    }
    document.update(overrides)
    return document


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_relation_pointing_at_the_root_is_a_collection() -> None:
    projection = _parse(_document())[0]
    assert projection.name == "ProcessoConsultar"
    assert projection.root == "Processo"
    member = projection.members[0]
    assert member.alias == "publicacoes"
    assert member.concept_type == "Publicacao"
    assert member.collection is True
    assert member.optional is False
    assert member.foreign_key.columns == ("processo",)


def test_relation_on_the_root_is_a_single_value() -> None:
    document = _document(
        name="ProcessoApenso",
        include=[{"relation": "Processo.apenso_de", "as": "apenso_de"}],
    )
    member = _parse(document)[0].members[0]
    assert member.collection is False
    assert member.concept_type == "Processo"


def test_optional_member_is_carried_through() -> None:
    document = _document(
        include=[{"relation": "Publicacao.processo", "as": "publicacoes", "optional": True}],
    )
    assert _parse(document)[0].members[0].optional is True


def test_composite_relation_resolves_by_any_participating_column() -> None:
    document = _document(
        name="PublicacaoConsultar",
        root="Publicacao",
        include=[{"relation": "EventoProcessual.publicacao_source_id", "as": "eventos"}],
    )
    member = _parse(document)[0].members[0]
    assert member.collection is True
    assert member.foreign_key.columns == ("publicacao_fonte", "publicacao_source_id")


def test_members_keep_their_declared_order() -> None:
    document = _document(
        include=[
            {"relation": "Processo.apenso_de", "as": "apenso_de"},
            {"relation": "Publicacao.processo", "as": "publicacoes"},
        ],
    )
    assert [member.alias for member in _parse(document)[0].members] == [
        "apenso_de",
        "publicacoes",
    ]


def test_projection_without_members_is_allowed() -> None:
    projection = _parse(_document(include=[]))[0]
    assert projection.members == ()


def test_projections_are_ordered_by_name() -> None:
    projections = parse_projections(
        [_document(name="Zeta"), _document(name="Alpha")],
        _foreign_keys(),
        concept_types=CONCEPT_TYPES,
    )
    assert [item.name for item in projections] == ["Alpha", "Zeta"]


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
def test_normative_errors(overrides: dict[str, object], expected: str) -> None:
    with pytest.raises(ProjectionError, match=expected):
        _parse(_document(**overrides))


def test_duplicate_projection_names_are_refused() -> None:
    with pytest.raises(ProjectionError, match="declared twice"):
        parse_projections(
            [_document(), _document()],
            _foreign_keys(),
            concept_types=CONCEPT_TYPES,
        )


def _bundle(tmp_path: Path) -> Path:
    _write(tmp_path / "okf.schema.sql", SCHEMA_SQL)
    _write(tmp_path / "processo.md", "---\ntype: Processo\ncnj: '1'\napenso_de: '1'\n---\n")
    _write(
        tmp_path / "publicacao.md",
        "---\ntype: Publicacao\nfonte: djen\nsource_id: a\nprocesso: '1'\n---\n",
    )
    _write(
        tmp_path / "projection.md",
        "---\ntype: Projection\nname: ProcessoConsultar\nroot: Processo\n"
        "include:\n  - relation: Publicacao.processo\n    as: publicacoes\n---\n",
    )
    return tmp_path


def test_load_projections_reads_the_bundle(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    projections = load_projections(str(root), relational_schema="okf.schema.sql")
    assert [item.name for item in projections] == ["ProcessoConsultar"]
    assert projections[0].members[0].collection is True


def test_load_projections_needs_a_relational_schema(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    with pytest.raises(ProjectionError, match="relational schema"):
        load_projections(str(root), relational_schema=None)


def test_a_bundle_with_no_projection_documents_yields_none(tmp_path: Path) -> None:
    _write(tmp_path / "okf.schema.sql", SCHEMA_SQL)
    _write(tmp_path / "processo.md", "---\ntype: Processo\ncnj: '1'\n---\n")
    assert load_projections(str(tmp_path), relational_schema="okf.schema.sql") == ()


def test_projection_documents_do_not_become_concept_types(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    contracts = build_schema_contracts(str(root), relational_schema="okf.schema.sql")
    assert "Projection" not in {contract.concept_type for contract in contracts}
    assert {contract.concept_type for contract in contracts} == {"Processo", "Publicacao"}
