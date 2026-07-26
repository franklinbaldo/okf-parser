"""End-to-end tests for bundle loading and conformance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_tools import Severity, load_bundle, validate_path

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_loads_concepts_reserved_documents_and_links(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "people" / "alice.md",
        "---\ntype: Person\ntitle: Alice\nextra: preserved\n---\n# Alice\n\nSee [Bob](bob.md).\n",
    )
    _write(tmp_path / "people" / "bob.md", "---\ntype: Person\n---\n# Bob\n")

    bundle = load_bundle(tmp_path)

    assert bundle.is_conformant
    assert bundle.concepts.count().execute() == 2
    assert bundle.reserved.count().execute() == 1
    link = bundle.links.execute().to_dict(orient="records")[0]
    assert link["source_id"] == "people/alice"
    assert link["target_id"] == "people/bob"
    assert link["exists"]
    assert link["origin"] == "body"


def test_reports_all_parse_and_type_errors(tmp_path: Path) -> None:
    _write(tmp_path / "missing-frontmatter.md", "# Not a concept\n")
    _write(tmp_path / "missing-type.md", "---\ntitle: No type\n---\nBody\n")

    bundle = load_bundle(tmp_path)

    assert not bundle.is_conformant
    assert {item.code for item in bundle.validate()} == {"OKF001", "OKF002"}

    report = validate_path(tmp_path)
    assert report.markdown_count == 2
    assert report.concept_count == 1
    assert report.reserved_count == 0
    assert not report.is_conformant


def test_broken_link_is_advisory_not_nonconformant(tmp_path: Path) -> None:
    _write(
        tmp_path / "source.md",
        "---\ntype: Reference\n---\nSee [missing](missing.md).\n",
    )

    bundle = load_bundle(tmp_path)

    assert bundle.is_conformant
    [diagnostic] = bundle.validate()
    assert diagnostic.code == "OKF101"
    assert diagnostic.severity is Severity.WARNING


def test_link_cannot_escape_bundle(tmp_path: Path) -> None:
    _write(
        tmp_path / "source.md",
        "---\ntype: Reference\n---\nSee [outside](../outside.md).\n",
    )

    bundle = load_bundle(tmp_path)

    assert bundle.links.count().execute() == 0


def test_networkx_graph_uses_the_same_relations(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Node\n---\n[B](b.md)\n")
    _write(tmp_path / "b.md", "---\ntype: Node\n---\n")

    graph = load_bundle(tmp_path).to_networkx()

    assert set(graph.nodes) == {"a", "b"}
    assert {(source, target) for source, target, _key in graph.edges} == {("a", "b")}


def test_frontmatter_links_are_relations_with_field_origin(tmp_path: Path) -> None:
    _write(
        tmp_path / "a.md",
        "---\ntype: Node\nrelated:\n  - /b.md\n---\n",
    )
    _write(tmp_path / "b.md", "---\ntype: Node\n---\n")

    bundle = load_bundle(tmp_path)
    [link] = bundle.links.execute().to_dict(orient="records")

    assert link["target_id"] == "b"
    assert link["origin"] == "frontmatter.related[0]"


def test_link_to_reserved_index_does_not_create_nan_graph_node(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(tmp_path / "a.md", "---\ntype: Node\n---\n[Index](index.md)\n")

    graph = load_bundle(tmp_path).to_networkx()

    assert set(graph.nodes) == {"a"}


def test_nested_index_frontmatter_is_nonconformant(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "---\nokf_version: '0.2'\n---\n# Root\n")
    _write(tmp_path / "nested" / "index.md", "---\nokf_version: '0.2'\n---\n# Nested\n")

    bundle = load_bundle(tmp_path)

    assert not bundle.is_conformant
    assert [item.code for item in bundle.validate()] == ["OKF004"]


def test_log_dates_must_be_real_and_newest_first(tmp_path: Path) -> None:
    _write(
        tmp_path / "log.md",
        "# Log\n\n## 2026-01-01\n* First\n\n## 2026-02-01\n* Second\n\n## 2026-13-40\n* Bad\n",
    )

    bundle = load_bundle(tmp_path)

    assert not bundle.is_conformant
    assert {item.code for item in bundle.validate()} == {"OKF008", "OKF009"}


def test_ignores_environment_and_version_control_markdown(tmp_path: Path) -> None:
    _write(tmp_path / "concept.md", "---\ntype: Project\n---\n# Project\n")
    _write(tmp_path / ".venv" / "README.md", "# Dependency documentation\n")
    _write(tmp_path / ".git" / "README.md", "# Git internals\n")
    _write(tmp_path / ".pytest_cache" / "README.md", "# Test internals\n")

    report = validate_path(tmp_path)

    assert report.is_conformant
    assert report.markdown_count == 1


def test_all_caps_markdown_is_reserved_and_frontmatter_is_optional(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Human documentation\n")
    _write(tmp_path / "CONTRIBUTING.md", "# Contributing\n")
    _write(tmp_path / "concept.md", "---\ntype: Project\n---\n# Project\n")

    report = validate_path(tmp_path)

    assert report.is_conformant
    assert report.markdown_count == 3
    assert report.concept_count == 1
    assert report.reserved_count == 2


def test_all_caps_frontmatter_can_be_required_by_policy(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Human documentation\n")

    report = validate_path(tmp_path, require_all_caps_frontmatter=True)

    assert not report.is_conformant
    assert [item.code for item in report.violations] == ["OKF011"]
