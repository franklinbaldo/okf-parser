"""Tests for RFC 0006 decision 11's specification-document scaffolding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.spec_scaffold import scaffold_missing_specs

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
