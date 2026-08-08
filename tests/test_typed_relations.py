"""Public in-process access to RFC 0006 typed Ibis relations."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import ibis
import pytest

from okf_parser import load_bundle
from okf_parser.declared_schema import DeclaredSchemaError

if TYPE_CHECKING:
    from pathlib import Path


def _declared_bundle(root: Path) -> str:
    (root / "a.md").write_text(
        "---\ntype: Rotina\ncusto: 12.50\ncodigo: alpha\n---\nA\n",
        encoding="utf-8",
    )
    (root / "b.md").write_text(
        "---\ntype: Rotina\ncusto: n/a\ncodigo: beta\n---\nB\n",
        encoding="utf-8",
    )
    types = root / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n',
        encoding="utf-8",
    )
    return "docs/types/{slug}.md"


def test_bundle_compile_types_returns_live_ibis_relations(tmp_path: Path) -> None:
    template = _declared_bundle(tmp_path)
    bundle = load_bundle(tmp_path)

    with bundle.compile_types(template) as typed:
        assert typed.tables == ("Rotina",)
        assert tuple(typed) == ("Rotina",)
        relation = typed["Rotina"]
        assert relation.schema()["custo"] == ibis.dtype("decimal(18,2)")
        assert relation.schema()["codigo"] == ibis.dtype("string")
        rows = relation.select("__okf_path", "custo", "codigo").order_by("__okf_path").execute()
        assert rows.to_dict(orient="records") == [
            {"__okf_path": "a.md", "custo": Decimal("12.50"), "codigo": "alpha"},
            {"__okf_path": "b.md", "custo": None, "codigo": "beta"},
        ]


def test_compile_types_without_declarations_is_empty_and_explicit(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")

    with load_bundle(tmp_path).compile_types() as typed:
        assert typed.tables == ()
        assert len(typed) == 0
        with pytest.raises(KeyError, match="Node"):
            typed["Node"]


def test_typed_relations_expose_bundle_diagnostics(tmp_path: Path) -> None:
    template = _declared_bundle(tmp_path)
    (tmp_path / "a.md").write_text(
        "---\ntype: Rotina\ncusto: 12.50\ncodigo: alpha\n---\n[Missing](missing.md)\n",
        encoding="utf-8",
    )

    with load_bundle(tmp_path).compile_types(template) as typed:
        assert [diagnostic.code for diagnostic in typed.diagnostics] == ["OKF101"]
        assert typed["Rotina"].count().execute() == 2


def test_typed_relations_close_is_explicit_and_idempotent(tmp_path: Path) -> None:
    template = _declared_bundle(tmp_path)
    typed = load_bundle(tmp_path).compile_types(template)

    typed.close()
    typed.close()

    with pytest.raises(RuntimeError, match="closed"):
        typed.relation("Rotina")


def test_compile_types_surfaces_declared_schema_failures(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")
    types = tmp_path / "docs" / "types"
    types.mkdir(parents=True)
    (types / "node.schema.sql").write_text(
        'CREATE TABLE "Other" (value BIGINT);\n', encoding="utf-8"
    )

    with pytest.raises(DeclaredSchemaError):
        load_bundle(tmp_path).compile_types("docs/types/{slug}.md")
