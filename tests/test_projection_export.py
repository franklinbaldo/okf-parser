"""RFC 0018 step 5: resolved projections export through every schema renderer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.schema_contract import ListNode, RefNode
from okf_parser.schema_export import (
    build_schema_contracts,
    export_json_schema,
    export_pydantic_source,
    export_zod_schema,
)

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


def _bundle(tmp_path: Path) -> Path:
    _write(tmp_path / "okf.schema.sql", SCHEMA_SQL)
    _write(tmp_path / "processo.md", "---\ntype: Processo\ncnj: '1'\napenso_de: '1'\n---\n")
    _write(
        tmp_path / "publicacao.md",
        "---\ntype: Publicacao\nfonte: djen\nsource_id: a\nprocesso: '1'\n---\n",
    )
    _write(
        tmp_path / "evento.md",
        "---\ntype: EventoProcessual\nid: e1\npublicacao_fonte: djen\n"
        "publicacao_source_id: a\n---\n",
    )
    _write(
        tmp_path / "projection-processo.md",
        "---\ntype: Projection\nname: ProcessoConsultar\nroot: Processo\n"
        "include:\n  - relation: Publicacao.processo\n    as: publicacoes\n---\n",
    )
    _write(
        tmp_path / "projection-publicacao.md",
        "---\ntype: Projection\nname: PublicacaoConsultar\nroot: Publicacao\n"
        "include:\n  - relation: EventoProcessual.publicacao_source_id\n"
        "    as: eventos\n    optional: true\n---\n",
    )
    return tmp_path


def test_projection_contract_preserves_root_and_adds_composed_members(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    contracts = build_schema_contracts(str(root), relational_schema="okf.schema.sql")
    by_type = {contract.concept_type: contract for contract in contracts}

    projection = by_type["ProcessoConsultar"]
    fields = {field.name: field for field in projection.root.fields}
    assert {"type", "title", "description", "cnj", "apenso_de", "publicacoes"} <= set(fields)
    assert fields["publicacoes"].required is True
    assert fields["publicacoes"].nullable is False
    assert isinstance(fields["publicacoes"].value, ListNode)
    assert isinstance(fields["publicacoes"].value.item, RefNode)
    assert fields["publicacoes"].value.item.concept_type == "Publicacao"
    assert fields["publicacoes"].value.item.embedded is True


def test_optional_projection_member_is_nullable_but_present(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    contracts = build_schema_contracts(str(root), relational_schema="okf.schema.sql")
    [projection] = [item for item in contracts if item.concept_type == "PublicacaoConsultar"]
    [eventos] = [field for field in projection.root.fields if field.name == "eventos"]

    assert eventos.required is True
    assert eventos.nullable is True
    assert isinstance(eventos.value, ListNode)
    assert isinstance(eventos.value.item, RefNode)
    assert eventos.value.item.concept_type == "EventoProcessual"


def test_json_schema_exports_projection_refs_and_defs_in_key_mode(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    exported = export_json_schema(str(root), relational_schema="okf.schema.sql")

    assert "defs" in exported
    processo = exported["schemas"]["ProcessoConsultar"]
    assert processo["properties"]["publicacoes"]["items"]["$ref"] == "#/$defs/Publicacao"
    publicacao = exported["schemas"]["PublicacaoConsultar"]
    assert "eventos" in publicacao["required"]
    assert "anyOf" in publicacao["properties"]["eventos"]


def test_composite_relation_keeps_authored_alias_and_foreign_key_identity(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    contracts = build_schema_contracts(str(root), relational_schema="okf.schema.sql")
    [projection] = [item for item in contracts if item.concept_type == "PublicacaoConsultar"]
    [eventos] = [field for field in projection.root.fields if field.name == "eventos"]

    assert isinstance(eventos.value, ListNode)
    assert isinstance(eventos.value.item, RefNode)
    assert eventos.value.item.columns == ("publicacao_fonte", "publicacao_source_id")
    assert eventos.value.item.referenced_columns == ("fonte", "source_id")


def test_zod_and_pydantic_emit_projection_models(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    zod = export_zod_schema(str(root), relational_schema="okf.schema.sql")
    pydantic = export_pydantic_source(str(root), relational_schema="okf.schema.sql")

    assert "export const ProcessoConsultarProjectionSchema" in zod
    assert '"publicacoes": z.array(PublicacaoConceptSchema)' in zod
    assert "class ProcessoConsultarProjection(BaseModel):" in pydantic
    assert "publicacoes: list[PublicacaoConcept]" in pydantic
