"""Pin RFC 0023 concept identity and rename semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser import concept, load_bundle, resolve_relations

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_rename_is_removal_plus_addition_not_a_tracked_move(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    old_path = tmp_path / "notes/a.md"
    _write(old_path, "---\ntype: note\ntitle: Original\n---\nBody.\n")

    before = load_bundle(tmp_path, engine="native")
    original = concept(before, "notes/a")

    old_path.rename(tmp_path / "notes/b.md")
    after = load_bundle(tmp_path, engine="native")

    with pytest.raises(KeyError, match="not found"):
        concept(after, "notes/a")

    moved = concept(after, "notes/b")
    assert moved.concept_id == "notes/b"
    assert moved.logical_key == "notes/b"
    assert moved != original


def test_rename_preserves_parsed_digest_as_advisory_continuity_evidence(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    old_path = tmp_path / "notes/a.md"
    text = "---\ntype: note\ntitle: Original\n---\nBody.\n"
    _write(old_path, text)

    before = load_bundle(tmp_path, engine="native")
    original = concept(before, "notes/a")

    old_path.rename(tmp_path / "notes/b.md")
    after = load_bundle(tmp_path, engine="native")
    moved = concept(after, "notes/b")

    # Same content, unrelated ids: a caller may compute this as evidence of a
    # possible move, but the parser never merges identity on its own.
    assert moved.parsed_digest == original.parsed_digest
    assert moved.source_digest == original.source_digest
    assert moved.concept_id != original.concept_id


def test_rename_does_not_migrate_a_stale_frontmatter_reference(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "notes/source.md",
        "---\ntype: source\ntitle: Source\n---\nSource body.\n",
    )
    _write(
        tmp_path / "notes/ready.md",
        "---\ntype: article-ready\nsources:\n  - resource: notes/source.md\n---\nBody.\n",
    )

    bundle = load_bundle(tmp_path, engine="native")
    resolved = resolve_relations(bundle, "notes/ready.md", field="sources")
    assert [item.concept_id for item in resolved] == ["notes/source"]

    (tmp_path / "notes/source.md").rename(tmp_path / "notes/moved-source.md")
    moved_bundle = load_bundle(tmp_path, engine="native")

    with pytest.raises(KeyError, match="not found"):
        resolve_relations(moved_bundle, "notes/ready.md", field="sources")

    # The concept survives at its new id; only the stale inbound reference breaks.
    assert concept(moved_bundle, "notes/moved-source").concept_id == "notes/moved-source"


def test_content_edited_and_moved_together_diverges_the_digest(tmp_path: Path) -> None:
    """A move plus an edit is not "the same content", by digest, at the new id."""
    _write(tmp_path / "index.md", "# Bundle\n")
    old_path = tmp_path / "notes/a.md"
    _write(old_path, "---\ntype: note\ntitle: Original\n---\nBody.\n")

    before = load_bundle(tmp_path, engine="native")
    original = concept(before, "notes/a")

    old_path.rename(tmp_path / "notes/b.md")
    _write(tmp_path / "notes/b.md", "---\ntype: note\ntitle: Original\n---\nEdited body.\n")
    after = load_bundle(tmp_path, engine="native")
    moved = concept(after, "notes/b")

    assert moved.parsed_digest != original.parsed_digest
