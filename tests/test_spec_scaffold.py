"""Tests for RFC 0006 decision 11's specification-document scaffolding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.spec_scaffold import scaffold_missing_declared_schemas, scaffold_missing_specs

if TYPE_CHECKING:
    from pathlib import Path

TEMPLATE = "docs/types/{slug}.md"


def test_dry_run_reports_would_create_and_writes_nothing(tmp_path: Path) -> None:
    result = scaffold_missing_specs(tmp_path, {"Rotina"}, TEMPLATE)

    assert result == {
        "created": [],
        "would_create": ["docs/types/rotina.md"],
        "collisions": [],
        "written": False,
    }
    assert not (tmp_path / "docs/types/rotina.md").exists()


def test_write_creates_a_stub_document(tmp_path: Path) -> None:
    result = scaffold_missing_specs(tmp_path, {"Rotina"}, TEMPLATE, write=True)

    assert result["created"] == ["docs/types/rotina.md"]
    assert result["written"] is True
    content = (tmp_path / "docs/types/rotina.md").read_text(encoding="utf-8")
    assert content.startswith("---\ntype: Spec\n---\n\n# Rotina\n")


def test_existing_document_is_never_overwritten(tmp_path: Path) -> None:
    spec = tmp_path / "docs/types/rotina.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("---\ntype: Spec\n---\n\n# Custom\n", encoding="utf-8")

    result = scaffold_missing_specs(tmp_path, {"Rotina"}, TEMPLATE, write=True)

    assert result == {"created": [], "would_create": [], "collisions": [], "written": True}
    assert spec.read_text(encoding="utf-8") == "---\ntype: Spec\n---\n\n# Custom\n"


def test_derived_path_collision_blocks_every_write_for_the_call(tmp_path: Path) -> None:
    result = scaffold_missing_specs(
        tmp_path, {"Revisao Ciencia", "Revisão Ciência", "Rotina"}, TEMPLATE, write=True
    )

    assert result["written"] is False
    assert result["created"] == []
    assert result["collisions"] == [
        {
            "path": "docs/types/revisao-ciencia.md",
            "types": ["Revisao Ciencia", "Revisão Ciência"],
        }
    ]
    # The non-colliding type is not scaffolded either - fail-closed for the whole call.
    assert not (tmp_path / "docs/types/rotina.md").exists()


def test_scaffold_declared_schema_dry_run(tmp_path: Path) -> None:
    documents: dict[str, list[dict[str, object]]] = {
        "Rotina": [
            {"type": "Rotina", "custo": "10.50", "registrado_em": "2026-01-15T09:30:00Z"},
            {"type": "Rotina", "custo": "7", "registrado_em": "2026-02-01T14:00:00Z"},
        ]
    }

    result = scaffold_missing_declared_schemas(tmp_path, TEMPLATE, documents)

    assert result["written"] is False
    assert result["created"] == []
    assert result["would_create"] == ["docs/types/rotina.schema.sql"]


def test_scaffold_declared_schema_writes_inferred_types(tmp_path: Path) -> None:
    documents: dict[str, list[dict[str, object]]] = {
        "Rotina": [
            {
                "type": "Rotina",
                "custo": "10.50",
                "registrado_em": "2026-01-15T09:30:00Z",
                "nome": "Ana",
            },
            {
                "type": "Rotina",
                "custo": "7",
                "registrado_em": "2026-02-01T14:00:00Z",
                "nome": "Beto",
            },
        ]
    }

    result = scaffold_missing_declared_schemas(tmp_path, TEMPLATE, documents, write=True)

    assert result["created"] == ["docs/types/rotina.schema.sql"]
    content = (tmp_path / "docs/types/rotina.schema.sql").read_text(encoding="utf-8")
    assert 'CREATE TABLE "Rotina"' in content
    assert '"custo" DOUBLE' in content
    assert '"nome" VARCHAR' in content
    assert '"registrado_em" TIMESTAMPTZ' in content


def test_scaffold_declared_schema_skips_a_type_with_no_scalar_fields(tmp_path: Path) -> None:
    documents: dict[str, list[dict[str, object]]] = {"Vazio": [{"type": "Vazio"}]}

    result = scaffold_missing_declared_schemas(tmp_path, TEMPLATE, documents, write=True)

    assert result == {"created": [], "would_create": [], "collisions": [], "written": True}


def test_scaffold_declared_schema_skips_a_field_that_is_structured_on_any_document(
    tmp_path: Path,
) -> None:
    documents: dict[str, list[dict[str, object]]] = {
        "Rotina": [
            {"type": "Rotina", "custo": "10.50", "tags": ["a", "b"]},
            {"type": "Rotina", "custo": "7", "tags": "not-a-list-here"},
        ]
    }

    scaffold_missing_declared_schemas(tmp_path, TEMPLATE, documents, write=True)

    content = (tmp_path / "docs/types/rotina.schema.sql").read_text(encoding="utf-8")
    assert "tags" not in content
    assert '"custo" DOUBLE' in content


def test_scaffold_declared_schema_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    existing = tmp_path / "docs/types/rotina.schema.sql"
    existing.parent.mkdir(parents=True)
    existing.write_text('CREATE TABLE "Rotina" (custo VARCHAR);', encoding="utf-8")
    documents: dict[str, list[dict[str, object]]] = {
        "Rotina": [{"type": "Rotina", "custo": "10.50"}]
    }

    result = scaffold_missing_declared_schemas(tmp_path, TEMPLATE, documents, write=True)

    assert result == {"created": [], "would_create": [], "collisions": [], "written": True}
    assert "VARCHAR" in existing.read_text(encoding="utf-8")


def test_scaffold_declared_schema_collision_blocks_the_whole_call(tmp_path: Path) -> None:
    documents: dict[str, list[dict[str, object]]] = {
        "Revisao Ciencia": [{"type": "Revisao Ciencia", "custo": "10.50"}],
        "Revisão Ciência": [{"type": "Revisão Ciência", "custo": "20.00"}],
        "Rotina": [{"type": "Rotina", "custo": "7"}],
    }

    result = scaffold_missing_declared_schemas(tmp_path, TEMPLATE, documents, write=True)

    assert result["written"] is False
    assert result["created"] == []
    assert not (tmp_path / "docs/types/rotina.schema.sql").exists()
