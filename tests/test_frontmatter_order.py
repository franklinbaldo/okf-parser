"""Tests for the non-semantic canonical physical frontmatter order."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.formatting import format_path
from okf_parser.parser import parse_document, parse_document_text

if TYPE_CHECKING:
    from pathlib import Path


def _formatted_frontmatter(root: Path, block: str) -> str:
    document = root / "concept.md"
    document.write_text(f"---\n{block}\n---\n\n# Body\n", encoding="utf-8")
    format_path(root, write=True)
    text = document.read_text(encoding="utf-8")
    return text.removeprefix("---\n").split("\n---\n", 1)[0]


def test_simple_frontmatter_is_reordered_without_changing_scalar_spelling(tmp_path: Path) -> None:
    source = (
        "status: active\n"
        "description: Plain text\n"
        "type: Reference\n"
        "number: 0012\n"
        "title: Example\n"
        "active: false"
    )

    assert _formatted_frontmatter(tmp_path, source) == (
        "type: Reference\n"
        "title: Example\n"
        "description: Plain text\n"
        "active: false\n"
        "number: 0012\n"
        "status: active"
    )


def test_complex_or_commented_frontmatter_is_left_byte_for_byte(tmp_path: Path) -> None:
    cases = (
        "type: Reference # keep me\ntitle: Example",
        "type: Reference\nitems:\n  - one\n  - two",
        "type: 'Reference'\ntitle: Example",
        "type: Reference\ntitle: Example: subtitle",
    )

    for source in cases:
        assert _formatted_frontmatter(tmp_path, source) == source


def test_frontmatter_reordering_preserves_parsed_semantics(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    frontmatter = (
        "status: active\n"
        "description: Plain text\n"
        "type: Reference\n"
        "number: 0012\n"
        "title: Example\n"
        "active: false"
    )
    ordered = _formatted_frontmatter(tmp_path, frontmatter)
    body = "# Example\n"

    before = parse_document_text(path, f"---\n{frontmatter}\n---\n{body}")
    after = parse_document_text(path, f"---\n{ordered}\n---\n{body}")

    assert after.frontmatter == before.frontmatter
    assert after.body == before.body
    assert after.parsed_digest == before.parsed_digest
    assert after.source_digest != before.source_digest


def test_format_write_uses_canonical_frontmatter_order(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text(
        "---\n"
        "status: active\n"
        "description: Plain text\n"
        "type: Reference\n"
        "number: 0012\n"
        "title: Example\n"
        "active: false\n"
        "---\n"
        "# Example\n",
        encoding="utf-8",
    )
    before = parse_document(path)

    report = format_path(tmp_path, write=True)
    after = parse_document(path)

    assert report.succeeded
    assert path.read_text(encoding="utf-8").startswith(
        "---\n"
        "type: Reference\n"
        "title: Example\n"
        "description: Plain text\n"
        "active: false\n"
        "number: 0012\n"
        "status: active\n"
        "---\n"
    )
    assert after.frontmatter == before.frontmatter


def test_format_does_not_reorder_complex_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    source = "---\ntype: Reference\nitems:\n  - one\n  - two\n---\n# Example\n"
    path.write_text(source, encoding="utf-8")

    format_path(tmp_path, write=True)

    assert "type: Reference\nitems:\n  - one\n  - two" in path.read_text(encoding="utf-8")
