"""Pydantic source generation and shared naming-policy regressions."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from okf_parser.schema_contract import SchemaNameCollisionError
from okf_parser.schema_export import build_pydantic_models, export_pydantic_source
from okf_parser.service import schema_bundle

if TYPE_CHECKING:
    from pathlib import Path


def _write_concept(path: Path, frontmatter: str) -> None:
    path.write_text(f"---\n{frontmatter}---\nBody\n", encoding="utf-8")


def _source_model(source: str, name: str) -> type[BaseModel]:
    namespace: dict[str, Any] = {}
    code = compile(source, "<generated-okf-models>", "exec", dont_inherit=True)
    exec(code, namespace)  # noqa: S102
    return cast("type[BaseModel]", namespace[name])


def test_pydantic_source_happy_path_is_deterministic_and_importable(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "registro.md",
        "type: Registro\nvalor: '12.34'\nid_externo: '550e8400-e29b-41d4-a716-446655440000'\n",
    )
    spec = tmp_path / "docs" / "types" / "registro.schema.sql"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        'CREATE TABLE "Registro" (valor DECIMAL(18, 4), id_externo UUID);',
        encoding="utf-8",
    )

    first = export_pydantic_source(str(tmp_path), spec_template="docs/types/{slug}.md")
    second = export_pydantic_source(str(tmp_path), spec_template="docs/types/{slug}.md")
    via_service = schema_bundle(str(tmp_path), "pydantic", spec_template="docs/types/{slug}.md")

    assert first == second == via_service
    assert "class RegistroConcept(BaseModel):" in first
    assert "valor: Decimal" in first
    assert "id_externo: UUID" in first

    model = _source_model(first, "RegistroConcept")
    value = model.model_validate(
        {
            "type": "Registro",
            "valor": "12.34",
            "id_externo": "550e8400-e29b-41d4-a716-446655440000",
            "extension": "preserved",
        }
    )
    dumped = value.model_dump()
    assert dumped["valor"] == Decimal("12.34")
    assert dumped["id_externo"] == UUID("550e8400-e29b-41d4-a716-446655440000")
    assert value.model_extra == {"extension": "preserved"}


def test_dynamic_and_source_models_share_automatic_alias_policy(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "odd.md",
        "type: Odd\n_private: value\n__custom__: value\n__pydantic_extra__: value\n"
        "model_dump: value\nclass: value\ncustomer-id: value\n",
    )

    dynamic = build_pydantic_models(str(tmp_path))["Odd"]
    generated = _source_model(export_pydantic_source(str(tmp_path)), "OddConcept")

    expected = {
        "private_": "_private",
        "custom_": "__custom__",
        "pydantic_extra_": "__pydantic_extra__",
        "field_model_dump": "model_dump",
        "class_": "class",
        "customer_id": "customer-id",
    }
    for model in (dynamic, generated):
        aliases = {name: field.alias for name, field in model.model_fields.items()}
        for name, alias in expected.items():
            assert aliases[name] == alias
        value = model.model_validate(
            {
                "type": "Odd",
                "_private": "value",
                "__custom__": "value",
                "__pydantic_extra__": "value",
                "model_dump": "value",
                "class": "value",
                "customer-id": "value",
            }
        )
        assert value.model_dump()["private_"] == "value"
        with pytest.raises(ValidationError):
            model.model_validate(
                {
                    "type": "Odd",
                    "_private": None,
                    "__custom__": "value",
                    "__pydantic_extra__": "value",
                    "model_dump": "value",
                    "class": "value",
                    "customer-id": "value",
                }
            )


def test_optional_non_nullable_accepts_omission_but_rejects_null(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: Pessoa\napelido: presente\n")
    _write_concept(tmp_path / "two.md", "type: Pessoa\n")

    dynamic = build_pydantic_models(str(tmp_path))["Pessoa"]
    generated = _source_model(export_pydantic_source(str(tmp_path)), "PessoaConcept")

    for model in (dynamic, generated):
        omitted = model.model_validate({"type": "Pessoa"})
        assert omitted.model_dump()["apelido"] is None
        with pytest.raises(ValidationError):
            model.model_validate({"type": "Pessoa", "apelido": None})


def test_field_alias_collision_fails_in_both_pydantic_projections(tmp_path: Path) -> None:
    _write_concept(tmp_path / "odd.md", "type: Odd\n_x: one\nx_: two\n")

    with pytest.raises(SchemaNameCollisionError, match="both map to Pydantic attribute 'x_'"):
        build_pydantic_models(str(tmp_path))
    with pytest.raises(SchemaNameCollisionError, match="both map to Pydantic attribute 'x_'"):
        export_pydantic_source(str(tmp_path))


def test_nested_model_name_collision_reports_both_structural_paths(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "x.md",
        "type: X\na:\n  structure_b:\n    value: one\na_structure:\n  b:\n    other: two\n",
    )

    for operation in (build_pydantic_models, export_pydantic_source):
        with pytest.raises(SchemaNameCollisionError) as caught:
            operation(str(tmp_path))
        message = str(caught.value)
        assert "'a' -> 'structure_b'" in message
        assert "'a_structure' -> 'b'" in message
