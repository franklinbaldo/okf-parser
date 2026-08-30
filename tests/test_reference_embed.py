"""Tests for RFC 0018 `--refs=embed`: references emitted by sibling name."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from okf_parser.schema_contract import RefNode
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

# DuckDB cannot express a two-table cycle in DDL (no `ALTER TABLE ADD FOREIGN
# KEY`), and a self-reference is the minimal cycle anyway: a schema that must
# name itself is exactly the case a structural inline cannot survive.
CYCLIC_SQL = """
CREATE TABLE "Processo" (
    cnj VARCHAR PRIMARY KEY,
    apenso_de VARCHAR REFERENCES "Processo"(cnj)
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
def bundle(tmp_path: Path) -> Path:
    _write(tmp_path / "regra.md", _concept("Regra", nome="regra-a"))
    _write(tmp_path / "fund.md", _concept("Fundamentacao", id="f1", regra="regra-a"))
    _write(tmp_path / "okf.schema.sql", SINGLE_COLUMN_SQL)
    return tmp_path


@pytest.fixture
def cyclic_bundle(tmp_path: Path) -> Path:
    # the root process has no parent, which makes `apenso_de` optional — a
    # required self-reference would demand an infinitely nested document
    _write(tmp_path / "raiz.md", _concept("Processo", cnj="1"))
    _write(tmp_path / "apenso.md", _concept("Processo", cnj="2", apenso_de="1"))
    _write(tmp_path / "okf.schema.sql", CYCLIC_SQL)
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


def test_embed_marks_the_reference_as_embedded(bundle: Path) -> None:
    contracts = build_schema_contracts(
        str(bundle), relational_schema="okf.schema.sql", refs="embed"
    )
    [fundamentacao] = [item for item in contracts if item.concept_type == "Fundamentacao"]
    [regra] = [item for item in fundamentacao.root.fields if item.name == "regra"]

    assert isinstance(regra.value, RefNode)
    assert regra.value.embedded


def test_json_schema_emits_a_ref_into_defs(bundle: Path) -> None:
    exported = export_json_schema(str(bundle), relational_schema="okf.schema.sql", refs="embed")

    assert set(exported["defs"]) == {"Fundamentacao", "Regra"}
    regra = exported["schemas"]["Fundamentacao"]["properties"]["regra"]
    assert regra["$ref"] == "#/$defs/Regra"


def test_json_schema_keeps_the_reference_metadata_when_embedded(bundle: Path) -> None:
    exported = export_json_schema(str(bundle), relational_schema="okf.schema.sql", refs="embed")
    regra = exported["schemas"]["Fundamentacao"]["properties"]["regra"]

    assert regra["x-okf-references"]["type"] == "Regra"


def test_zod_emits_the_sibling_variable(bundle: Path) -> None:
    source = export_zod_schema(str(bundle), relational_schema="okf.schema.sql", refs="embed")

    assert '"regra": RegraSchema' in source
    # the referenced schema has to be declared before the schema that uses it
    assert source.index("export const RegraSchema") < source.index(
        "export const FundamentacaoSchema"
    )


def test_zod_closes_a_cycle_with_lazy(cyclic_bundle: Path) -> None:
    source = export_zod_schema(str(cyclic_bundle), relational_schema="okf.schema.sql", refs="embed")

    assert "z.lazy(() =>" in source
    assert source.count("z.lazy(() =>") == 1


def test_pydantic_emits_a_forward_reference_and_rebuilds(cyclic_bundle: Path) -> None:
    source = export_pydantic_source(
        str(cyclic_bundle), relational_schema="okf.schema.sql", refs="embed"
    )

    assert "from __future__ import annotations" in source
    assert "model_rebuild()" in source


def test_pydantic_annotates_the_referenced_model(bundle: Path) -> None:
    source = export_pydantic_source(str(bundle), relational_schema="okf.schema.sql", refs="embed")

    assert "regra: Regra" in source


def test_embedding_a_composite_key_is_refused(composite_bundle: Path) -> None:
    with pytest.raises(SchemaExportError, match="composite"):
        build_schema_contracts(
            str(composite_bundle), relational_schema="okf.schema.sql", refs="embed"
        )


def test_composite_key_is_still_fine_in_key_mode(composite_bundle: Path) -> None:
    exported = export_json_schema(str(composite_bundle), relational_schema="okf.schema.sql")

    assert "x-okf-references" in json.dumps(exported["schemas"]["Evento"])


def test_key_mode_output_is_unchanged_by_the_new_flag(bundle: Path) -> None:
    default = export_json_schema(str(bundle), relational_schema="okf.schema.sql")
    explicit = export_json_schema(str(bundle), relational_schema="okf.schema.sql", refs="key")

    assert default == explicit
    assert "defs" not in default


def test_embed_without_a_relational_schema_is_refused(bundle: Path) -> None:
    with pytest.raises(SchemaExportError, match="relational schema"):
        build_schema_contracts(str(bundle), refs="embed")


def test_generated_pydantic_source_with_a_cycle_imports_and_validates(
    cyclic_bundle: Path, tmp_path: Path
) -> None:
    source = export_pydantic_source(
        str(cyclic_bundle), relational_schema="okf.schema.sql", refs="embed"
    )
    module_path = tmp_path / "generated_models.py"
    module_path.write_text(source, encoding="utf-8")
    namespace: dict[str, object] = {}

    exec(compile(source, str(module_path), "exec"), namespace)  # noqa: S102 — generated by us

    model = namespace["ProcessoConcept"]
    assert isinstance(model, type)
    assert issubclass(model, BaseModel)
    instance = model.model_validate(
        {"type": "Processo", "cnj": "2", "apenso_de": {"type": "Processo", "cnj": "1"}}
    )
    dumped = instance.model_dump()
    assert dumped["apenso_de"]["cnj"] == "1"


def test_cli_schema_accepts_the_refs_flag(bundle: Path) -> None:
    payload = schema_bundle(str(bundle), "json", relational_schema="okf.schema.sql", refs="embed")

    assert isinstance(payload, dict)
    assert "defs" in payload
