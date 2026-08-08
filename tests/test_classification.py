"""Explain strict bundle membership without changing conformance semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.classification import classify_path
from okf_parser.service import check_bundle

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_classification_explains_concepts_reserved_ignored_and_invalid(tmp_path: Path) -> None:
    _write(tmp_path / "concept.md", "---\ntype: Note\n---\n# Concept\n")
    _write(tmp_path / "untyped.md", "---\ntitle: Untyped\n---\n# Untyped\n")
    _write(tmp_path / "broken.md", "# No frontmatter\n")
    _write(tmp_path / "docs" / "index.md", "# Docs\n")
    _write(tmp_path / ".claude" / "skills" / "hidden.md", "---\ntype: Note\n---\n")
    _write(tmp_path / ".agents" / "skills" / "hidden.md", "---\ntype: Note\n---\n")
    _write(tmp_path / "docs" / "excluded" / "hidden.md", "---\ntype: Note\n---\n")
    (tmp_path / ".okfignore").write_text(".claude/\n.agents/\n/docs/excluded/\n", encoding="utf-8")

    classification = classify_path(tmp_path)

    assert classification == {
        "concepts": ["concept.md"],
        "reserved": ["docs/index.md"],
        "ignored": [
            ".agents/skills/hidden.md",
            ".claude/skills/hidden.md",
            "docs/excluded/hidden.md",
        ],
        "invalid_or_untyped": ["broken.md", "untyped.md"],
    }


def test_classification_honors_one_off_exclusions_without_reclassifying_core(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "keep.md", "---\ntype: Note\n---\n")
    _write(tmp_path / "skip.md", "---\ntype: Note\n---\n")

    classification = classify_path(tmp_path, ("skip.md",))

    assert classification["concepts"] == ["keep.md"]
    assert classification["ignored"] == ["skip.md"]
    assert classification["invalid_or_untyped"] == []


def test_check_classification_is_opt_in_and_does_not_change_conformance(tmp_path: Path) -> None:
    _write(tmp_path / "good.md", "---\ntype: Note\n---\n")
    _write(tmp_path / "bad.md", "# Bad\n")

    ordinary = check_bundle(str(tmp_path))
    explained = check_bundle(str(tmp_path), classify=True)

    assert "classification" not in ordinary
    assert explained["conformant"] is ordinary["conformant"] is False
    assert explained["diagnostics"] == ordinary["diagnostics"]
    assert explained["classification"] == {
        "concepts": ["good.md"],
        "reserved": [],
        "ignored": [],
        "invalid_or_untyped": ["bad.md"],
    }
