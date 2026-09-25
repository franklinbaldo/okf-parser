"""Regression tests for the parser/validator responsibility boundary.

`docs/architecture.md` states the rule: the core parser reads and preserves
documents, while normative conformance rules live in the validator
(`okf_parser.bundle`). These tests pin that boundary down so a future change
cannot quietly move semantic policy into `parse_document` without a test
failing here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser import load_bundle
from okf_parser.parser import parse_document

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_parser_accepts_frontmatter_with_no_type_field(tmp_path: Path) -> None:
    """Missing `type` is a validator concern (OKF002), not a parse failure."""
    path = tmp_path / "concept.md"
    _write(path, "---\ntitle: No type here\n---\nBody\n")

    parsed = parse_document(path)

    assert parsed.concept_type == ""
    assert parsed.frontmatter == {"title": "No type here"}


def test_parser_accepts_any_producer_defined_type_string(tmp_path: Path) -> None:
    """The parser imposes no taxonomy: any non-empty type string parses cleanly."""
    path = tmp_path / "concept.md"
    _write(path, "---\ntype: TotallyMadeUpType\n---\nBody\n")

    parsed = parse_document(path)

    assert parsed.concept_type == "TotallyMadeUpType"


def test_parser_preserves_unknown_frontmatter_fields_verbatim(tmp_path: Path) -> None:
    """Producer-defined frontmatter keys survive parsing untouched, as data."""
    path = tmp_path / "concept.md"
    _write(
        path,
        "---\ntype: Reference\nunexpected_field: preserved\nnested:\n  also: kept\n---\nBody\n",
    )

    parsed = parse_document(path)

    assert parsed.frontmatter["unexpected_field"] == "preserved"
    assert parsed.frontmatter["nested"] == {"also": "kept"}


def test_validator_not_parser_rejects_missing_type(tmp_path: Path) -> None:
    """`load_bundle` reports OKF002 for a document the parser accepted."""
    _write(tmp_path / "missing-type.md", "---\ntitle: No type\n---\nBody\n")

    parse_document(tmp_path / "missing-type.md")

    bundle = load_bundle(tmp_path)

    assert bundle.concepts.count().execute() == 1
    assert {item.code for item in bundle.validate()} == {"OKF002"}


def test_validator_accepts_unrecognized_type_without_taxonomy(tmp_path: Path) -> None:
    """A non-empty producer-defined type is conformant with no type registry."""
    _write(tmp_path / "concept.md", "---\ntype: NobodyDefinedThis\n---\nBody\n")

    bundle = load_bundle(tmp_path)

    assert bundle.is_conformant
    assert bundle.concepts.execute().to_dict(orient="records")[0]["concept_type"] == (
        "NobodyDefinedThis"
    )
