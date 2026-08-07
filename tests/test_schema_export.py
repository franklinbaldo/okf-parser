"""Tests for string-first JSON Schema and Zod export."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from okf_parser.schema_export import (
    SchemaCastError,
    SchemaNameCollisionError,
    build_pydantic_models,
    export_json_schema,
    export_zod_schema,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_concept(path: Path, frontmatter: str) -> None:
    path.write_text(f"---\n{frontmatter}---\nBody\n", encoding="utf-8")


def test_schema_keeps_scalars_as_strings_by_default(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "sample.md",
        "type: test_type\nactive: true\ncount: 42\ncreated: 2026-01-01\n",
    )

    schema = export_json_schema(str(tmp_path))["schemas"]["test_type"]
    properties = schema["properties"]

    assert properties["active"]["type"] == "string"
    assert properties["count"]["type"] == "string"
    assert properties["created"]["type"] == "string"
    assert properties["type"]["const"] == "test_type"


def test_schema_infers_types_from_every_observation(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "one.md",
        "type: test_type\nactive: true\ncount: 42\ncreated: 2026-01-01\n",
    )
    _write_concept(
        tmp_path / "two.md",
        "type: test_type\nactive: false\ncount: 7\ncreated: 2026-02-01\n",
    )

    report = export_json_schema(str(tmp_path), infer_types=True)
    properties = report["schemas"]["test_type"]["properties"]

    assert report["inferred_types"] is True
    assert properties["active"]["type"] == "boolean"
    assert properties["count"]["type"] == "integer"
    assert properties["created"] == {
        "format": "date",
        "title": "Created",
        "type": "string",
    }


def test_inference_falls_back_to_string_when_one_value_is_incompatible(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: test_type\ncount: 42\n")
    _write_concept(tmp_path / "two.md", "type: test_type\ncount: unknown\n")

    schema = export_json_schema(str(tmp_path), infer_types=True)["schemas"]["test_type"]

    assert schema["properties"]["count"]["type"] == "string"


def test_explicit_cast_overrides_default_and_is_strict(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: test_type\ncount: 42\n")
    _write_concept(tmp_path / "two.md", "type: test_type\ncount: 7\n")

    report = export_json_schema(str(tmp_path), casts=["count=integer"])

    assert report["casts"] == ["count=integer"]
    assert report["schemas"]["test_type"]["properties"]["count"]["type"] == "integer"

    with pytest.raises(SchemaCastError, match="cannot cast 'count' to date"):
        export_json_schema(str(tmp_path), casts=["count=date"])


def test_declared_schema_types_a_column_that_has_no_explicit_cast(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: rotina\ncusto: '10.50'\n")
    _write_concept(tmp_path / "two.md", "type: rotina\ncusto: '7'\n")
    spec = tmp_path / "docs" / "types" / "rotina.schema.sql"
    spec.parent.mkdir(parents=True)
    spec.write_text('CREATE TABLE "rotina" (custo DECIMAL(18, 4));', encoding="utf-8")

    report = export_json_schema(str(tmp_path), spec_template="docs/types/{slug}.md")

    assert report["schemas"]["rotina"]["properties"]["custo"]["type"] == "number"


def test_declared_schema_keeps_declared_intent_when_data_diverges(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: rotina\ncusto: '10.50'\n")
    _write_concept(tmp_path / "two.md", "type: rotina\ncusto: not-a-number\n")
    spec = tmp_path / "docs" / "types" / "rotina.schema.sql"
    spec.parent.mkdir(parents=True)
    spec.write_text('CREATE TABLE "rotina" (custo DECIMAL(18, 4));', encoding="utf-8")

    report = export_json_schema(str(tmp_path), spec_template="docs/types/{slug}.md")
    custo = report["schemas"]["rotina"]["properties"]["custo"]

    assert custo["type"] == "number"
    assert custo["multipleOf"] == 0.0001
    assert custo["x-okf-duckdb-type"] == "DECIMAL(18,4)"


def test_declared_schema_is_ignored_without_spec_template(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: rotina\ncusto: '10.50'\n")
    spec = tmp_path / "docs" / "types" / "rotina.schema.sql"
    spec.parent.mkdir(parents=True)
    spec.write_text('CREATE TABLE "rotina" (custo DECIMAL(18, 4));', encoding="utf-8")

    report = export_json_schema(str(tmp_path))

    assert report["schemas"]["rotina"]["properties"]["custo"]["type"] == "string"


def test_explicit_cast_wins_over_declared_schema(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: rotina\ncusto: '10.50'\n")
    spec = tmp_path / "docs" / "types" / "rotina.schema.sql"
    spec.parent.mkdir(parents=True)
    spec.write_text('CREATE TABLE "rotina" (custo DECIMAL(18, 4));', encoding="utf-8")

    report = export_json_schema(
        str(tmp_path), casts=["custo=string"], spec_template="docs/types/{slug}.md"
    )

    assert report["schemas"]["rotina"]["properties"]["custo"]["type"] == "string"


def test_declared_schema_is_scoped_per_type_not_leaked_across_shared_field_names(
    tmp_path: Path,
) -> None:
    """A `.schema.sql` for one type must never type another type's same-named field.

    Regression test: `_declared_casts` used to flatten every type's
    declared columns into one global `field=kind` list fed straight to
    `compile_contracts`, so a `Fatura.valor` declared as `DECIMAL` would
    also type an unrelated `Pesquisa.valor` field purely because both
    types happen to use the name `valor`.
    """
    _write_concept(tmp_path / "fatura.md", "type: Fatura\nvalor: '10.50'\n")
    _write_concept(tmp_path / "pesquisa.md", "type: Pesquisa\nvalor: not-a-number\n")

    docs_types = tmp_path / "docs" / "types"
    docs_types.mkdir(parents=True)
    (docs_types / "fatura.schema.sql").write_text(
        'CREATE TABLE "Fatura" (valor DECIMAL(18, 4));', encoding="utf-8"
    )
    # Pesquisa declares no .schema.sql at all - its `valor` must stay string,
    # not inherit Fatura's DECIMAL declaration by name alone.

    report = export_json_schema(str(tmp_path), spec_template="docs/types/{slug}.md")

    assert report["schemas"]["Fatura"]["properties"]["valor"]["type"] == "number"
    assert report["schemas"]["Pesquisa"]["properties"]["valor"]["type"] == "string"


def test_declared_schema_lets_two_types_declare_the_same_field_differently(
    tmp_path: Path,
) -> None:
    """Two types may declare the same field name as different physical types.

    Both `Fatura.valor` (DECIMAL) and `Pesquisa.valor` (VARCHAR) are
    declared explicitly here - not merely "one declared, one not" as in the
    leak-detection test above - and must compile independently.
    """
    _write_concept(tmp_path / "fatura.md", "type: Fatura\nvalor: '10.50'\n")
    _write_concept(tmp_path / "pesquisa.md", "type: Pesquisa\nvalor: 'qualquer texto'\n")

    docs_types = tmp_path / "docs" / "types"
    docs_types.mkdir(parents=True)
    (docs_types / "fatura.schema.sql").write_text(
        'CREATE TABLE "Fatura" (valor DECIMAL(18, 4));', encoding="utf-8"
    )
    (docs_types / "pesquisa.schema.sql").write_text(
        'CREATE TABLE "Pesquisa" (valor VARCHAR);', encoding="utf-8"
    )

    report = export_json_schema(str(tmp_path), spec_template="docs/types/{slug}.md")

    assert report["schemas"]["Fatura"]["properties"]["valor"]["type"] == "number"
    assert report["schemas"]["Pesquisa"]["properties"]["valor"]["type"] == "string"


def test_unknown_or_invalid_cast_is_reported(tmp_path: Path) -> None:
    _write_concept(tmp_path / "sample.md", "type: test_type\ncount: 42\n")

    with pytest.raises(SchemaCastError, match="cast field was not found"):
        export_json_schema(str(tmp_path), casts=["missing=integer"])
    with pytest.raises(SchemaCastError, match="expected FIELD=TYPE"):
        export_json_schema(str(tmp_path), casts=["count=uuid"])


def test_requiredness_and_nullability_are_independent(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "one.md",
        "type: test_type\noptional_value: present\nnullable_value: null\n",
    )
    _write_concept(
        tmp_path / "two.md",
        "type: test_type\nnullable_value: present\n",
    )

    schema = export_json_schema(str(tmp_path))["schemas"]["test_type"]
    required = set(schema["required"])
    optional_property = schema["properties"]["optional_value"]
    nullable_property = schema["properties"]["nullable_value"]

    assert "optional_value" not in required
    assert optional_property["type"] == "string"
    assert "anyOf" not in optional_property

    assert "nullable_value" in required
    assert {item.get("type") for item in nullable_property["anyOf"]} == {
        "null",
        "string",
    }

    zod = export_zod_schema(str(tmp_path))

    assert '"optional_value": z.string().optional()' in zod
    assert '"nullable_value": z.string().nullable()' in zod
    assert '"nullable_value": z.string().nullable().optional()' not in zod


def test_list_items_are_inferred_together(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: test_type\nvalues: [1, 2]\n")
    _write_concept(tmp_path / "two.md", "type: test_type\nvalues: [3]\n")

    schema = export_json_schema(str(tmp_path), infer_types=True)["schemas"]["test_type"]

    assert schema["properties"]["values"]["items"]["type"] == "integer"


def test_nullability_inside_lists_is_preserved(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "sample.md",
        "type: test_type\nvalues: [null, 1]\nobjects:\n  - null\n  - name: Example\n",
    )

    schema = export_json_schema(str(tmp_path), infer_types=True)["schemas"]["test_type"]
    scalar_items = schema["properties"]["values"]["items"]["anyOf"]
    object_items = schema["properties"]["objects"]["items"]["anyOf"]

    assert {item.get("type") for item in scalar_items} == {"integer", "null"}
    assert any(item.get("type") == "object" for item in object_items)
    assert any(item.get("type") == "null" for item in object_items)

    zod = export_zod_schema(str(tmp_path), infer_types=True)

    assert '"values": z.array(z.number().int().nullable())' in zod
    assert '"objects": z.array(z.object({' in zod
    assert "}).nullable())" in zod


def test_unicode_names_are_preserved_and_collisions_are_errors(tmp_path: Path) -> None:
    _write_concept(tmp_path / "accent.md", "type: ação\n")
    _write_concept(tmp_path / "hyphen.md", "type: a-o\n")
    _write_concept(tmp_path / "japanese.md", "type: 日本\n")

    zod = export_zod_schema(str(tmp_path))

    assert "export const AçãoSchema =" in zod
    assert "export const AOSchema =" in zod
    assert "export const 日本Schema =" in zod

    collision = tmp_path / "collision"
    collision.mkdir()
    _write_concept(collision / "hyphen.md", "type: a-o\n")
    _write_concept(collision / "underscore.md", "type: a_o\n")

    with pytest.raises(SchemaNameCollisionError, match="both normalize to 'AOConcept'"):
        export_json_schema(str(collision))


def test_zod_uses_the_same_inferred_schema(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "sample.md",
        "type: test_type\nactive: true\ncount: 42\ncreated: 2026-01-01\n",
    )

    zod = export_zod_schema(str(tmp_path), infer_types=True)

    assert "export const TestTypeSchema = z.object({" in zod
    assert '"active": z.boolean()' in zod
    assert '"count": z.number().int()' in zod
    assert '"created": z.iso.date()' in zod
    assert '"type": z.literal("test_type")' in zod


def test_declared_export_distinguishes_temporal_uuid_and_list_types(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "one.md",
        "type: Evento\nlocal_em: '2026-08-07T09:30:00'\n"
        "instante: '2026-08-07T13:30:00Z'\n"
        "id_externo: '550e8400-e29b-41d4-a716-446655440000'\n"
        "codigos: [1, 2]\n",
    )
    spec = tmp_path / "docs" / "types" / "evento.schema.sql"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        'CREATE TABLE "Evento" ('
        "local_em TIMESTAMP, instante TIMESTAMPTZ, id_externo UUID, codigos BIGINT[]);",
        encoding="utf-8",
    )

    report = export_json_schema(str(tmp_path), spec_template="docs/types/{slug}.md")
    properties = report["schemas"]["Evento"]["properties"]

    assert properties["local_em"] == {
        "title": "Local_em",
        "type": "string",
        "x-okf-duckdb-type": "TIMESTAMP",
        "x-okf-temporal-kind": "timestamp-without-time-zone",
    }
    assert properties["instante"]["format"] == "date-time"
    assert properties["instante"]["x-okf-duckdb-type"] == "TIMESTAMP WITH TIME ZONE"
    assert properties["id_externo"]["format"] == "uuid"
    assert properties["codigos"]["type"] == "array"
    assert properties["codigos"]["x-okf-duckdb-type"] == "BIGINT[]"
    assert properties["codigos"]["items"]["type"] == "integer"
    assert properties["codigos"]["items"]["x-okf-duckdb-type"] == "BIGINT"

    zod = export_zod_schema(str(tmp_path), spec_template="docs/types/{slug}.md")
    assert '"local_em": z.string()' in zod
    assert '"instante": z.iso.datetime({ offset: true })' in zod
    assert '"id_externo": z.uuid()' in zod
    assert '"codigos": z.array(z.union([z.number().int(), z.bigint()]))' in zod


def test_declared_decimal_and_bigint_use_target_specific_representations(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: Financeiro\nvalor: '12.34'\nsequencia: '42'\n")
    spec = tmp_path / "docs" / "types" / "financeiro.schema.sql"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        'CREATE TABLE "Financeiro" (valor DECIMAL(18, 4), sequencia BIGINT);',
        encoding="utf-8",
    )

    report = export_json_schema(str(tmp_path), spec_template="docs/types/{slug}.md")
    properties = report["schemas"]["Financeiro"]["properties"]
    assert properties["valor"]["type"] == "number"
    assert properties["sequencia"]["type"] == "integer"

    zod = export_zod_schema(str(tmp_path), spec_template="docs/types/{slug}.md")
    assert '"valor": z.string()' in zod
    assert '"sequencia": z.union([z.number().int(), z.bigint()])' in zod


def test_declared_pydantic_models_keep_decimal_and_uuid_runtime_types(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "one.md",
        "type: Registro\nvalor: '12.34'\nid_externo: '550e8400-e29b-41d4-a716-446655440000'\n",
    )
    spec = tmp_path / "docs" / "types" / "registro.schema.sql"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        'CREATE TABLE "Registro" (valor DECIMAL(18, 4), id_externo UUID);',
        encoding="utf-8",
    )

    model = build_pydantic_models(str(tmp_path), spec_template="docs/types/{slug}.md")["Registro"]

    assert model.model_fields["valor"].annotation is Decimal
    assert model.model_fields["id_externo"].annotation is UUID


def test_unsupported_declared_type_keeps_duckdb_identity_without_fake_json_type(
    tmp_path: Path,
) -> None:
    _write_concept(tmp_path / "one.md", "type: Estruturado\ndados: qualquer\n")
    spec = tmp_path / "docs" / "types" / "estruturado.schema.sql"
    spec.parent.mkdir(parents=True)
    spec.write_text('CREATE TABLE "Estruturado" (dados STRUCT(a INTEGER));', encoding="utf-8")

    report = export_json_schema(str(tmp_path), spec_template="docs/types/{slug}.md")
    dados = report["schemas"]["Estruturado"]["properties"]["dados"]

    assert dados["x-okf-duckdb-type"] == "STRUCT(a INTEGER)"
    assert "type" not in dados
    assert '"dados": z.unknown()' in export_zod_schema(
        str(tmp_path), spec_template="docs/types/{slug}.md"
    )
