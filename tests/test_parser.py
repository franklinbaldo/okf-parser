"""Unit tests for frontmatter parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_tools.parser import DocumentParseError, parse_document

if TYPE_CHECKING:
    from pathlib import Path


def test_frontmatter_value_may_contain_triple_dash(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text(
        "---\ntype: Reference\nnote: before --- after\n---\n# Body\n",
        encoding="utf-8",
    )

    parsed = parse_document(path)

    assert parsed.frontmatter["note"] == "before --- after"
    assert parsed.body == "# Body\n"


def test_frontmatter_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("---\n- item\n---\n", encoding="utf-8")

    with pytest.raises(DocumentParseError, match="mapping"):
        parse_document(path)


def test_utf8_bom_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("\ufeff---\ntype: Reference\n---\nBody\n", encoding="utf-8")

    parsed = parse_document(path)

    assert parsed.frontmatter["type"] == "Reference"
