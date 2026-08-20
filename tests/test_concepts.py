"""Tests for application-facing OKF concept lookup and relation resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser import concept, load_bundle, resolve_relations

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_concept_resolves_by_id_or_markdown_path(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "knowledge/source.md",
        "---\ntype: source\ntitle: Example source\n---\nSource body.\n",
    )
    bundle = load_bundle(tmp_path, engine="python")

    by_id = concept(bundle, "knowledge/source")
    by_path = concept(bundle, "knowledge/source.md")

    assert by_id == by_path
    assert by_id.concept_type == "source"
    assert by_id.frontmatter["title"] == "Example source"
    assert by_id.body == "Source body.\n"


def test_resolve_relations_follows_bundle_concepts_and_filters_type(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "knowledge/ready.md",
        "---\n"
        "type: article-ready\n"
        "title: Ready\n"
        "sources:\n"
        "  - resource: knowledge/review.md\n"
        "  - resource: knowledge/source.md\n"
        "---\n"
        "Final body.\n",
    )
    _write(
        tmp_path / "knowledge/review.md",
        "---\ntype: editorial-review\ntitle: Review\n---\nReview body.\n",
    )
    _write(
        tmp_path / "knowledge/source.md",
        "---\ntype: source\ntitle: Primary\nresource: https://example.invalid/data\n---\nSource.\n",
    )
    bundle = load_bundle(tmp_path, engine="python")

    resolved = resolve_relations(
        bundle,
        "knowledge/ready.md",
        field="sources",
        target_type="source",
    )

    assert [item.concept_id for item in resolved] == ["knowledge/source"]
    assert resolved[0].frontmatter["resource"] == "https://example.invalid/data"


def test_resolve_relations_rejects_unknown_local_concept(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "ready.md",
        "---\n"
        "type: article-ready\n"
        "sources:\n"
        "  - resource: missing.md\n"
        "---\n"
        "Body.\n",
    )
    bundle = load_bundle(tmp_path, engine="python")

    with pytest.raises(KeyError, match="not found"):
        resolve_relations(bundle, "ready.md")


def test_resolve_relations_rejects_malformed_relation_shape(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "ready.md",
        "---\ntype: article-ready\nsources: nope\n---\nBody.\n",
    )
    bundle = load_bundle(tmp_path, engine="python")

    with pytest.raises(ValueError, match="must be a list"):
        resolve_relations(bundle, "ready.md")
