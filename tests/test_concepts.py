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


def test_concept_accepts_absolute_path_inside_bundle(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    source = tmp_path / "knowledge/source.md"
    _write(source, "---\ntype: source\ntitle: Example\n---\nBody.\n")
    bundle = load_bundle(tmp_path, engine="python")

    resolved = concept(bundle, source.resolve())

    assert resolved.concept_id == "knowledge/source"


def test_concept_rejects_path_outside_bundle(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    outside = tmp_path / "outside.md"
    _write(bundle_root / "index.md", "# Bundle\n")
    _write(bundle_root / "inside.md", "---\ntype: note\n---\nInside.\n")
    _write(outside, "---\ntype: note\n---\nOutside.\n")
    bundle = load_bundle(bundle_root, engine="python")

    with pytest.raises(ValueError, match="escapes bundle root"):
        concept(bundle, outside.resolve())


def test_resolve_relations_follows_bundle_concepts_and_filters_type(
    tmp_path: Path,
) -> None:
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


def test_resolve_relations_supports_custom_field_and_resource_key(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "knowledge/report.md",
        "---\n"
        "type: report\n"
        "citations:\n"
        "  - concept: knowledge/source.md\n"
        "---\n"
        "Report body.\n",
    )
    _write(
        tmp_path / "knowledge/source.md",
        "---\ntype: source\ntitle: Primary\n---\nSource body.\n",
    )
    bundle = load_bundle(tmp_path, engine="python")

    resolved = resolve_relations(
        bundle,
        "knowledge/report.md",
        field="citations",
        resource_key="concept",
        target_type="source",
    )

    assert [item.concept_id for item in resolved] == ["knowledge/source"]


def test_resolve_relations_rejects_forged_source_record(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "ready.md",
        "---\ntype: article-ready\nsources:\n  - resource: source.md\n---\nOriginal body.\n",
    )
    _write(tmp_path / "source.md", "---\ntype: source\n---\nSource.\n")
    bundle = load_bundle(tmp_path, engine="python")
    ready = concept(bundle, "ready.md")
    forged = ready.model_copy(update={"body": "Forged body.\n"})

    with pytest.raises(ValueError, match="does not belong to bundle"):
        resolve_relations(bundle, forged)


def test_resolve_relations_rejects_unknown_local_concept(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "ready.md",
        "---\ntype: article-ready\nsources:\n  - resource: missing.md\n---\nBody.\n",
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


@pytest.mark.parametrize(
    ("sources_yaml", "message"),
    [
        ("  - source.md\n", "relation must be a mapping"),
        ("  - resource: '   '\n", "must declare string resource"),
    ],
)
def test_resolve_relations_rejects_malformed_relation_entries(
    tmp_path: Path,
    sources_yaml: str,
    message: str,
) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "ready.md",
        "---\ntype: article-ready\nsources:\n" + sources_yaml + "---\nBody.\n",
    )
    bundle = load_bundle(tmp_path, engine="python")

    with pytest.raises(ValueError, match=message):
        resolve_relations(bundle, "ready.md")
