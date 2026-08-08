"""End-to-end tests for bundle loading and conformance."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from okf_parser import Severity, load_bundle, validate_path

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


def test_repeated_targets_preserve_each_authored_link(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Node\n---\n[B](b.md) and [B again](b.md)\n")
    _write(tmp_path / "b.md", "---\ntype: Node\n---\n")

    links = load_bundle(tmp_path).links.execute().to_dict(orient="records")

    assert [link["target_id"] for link in links] == ["b", "b"]


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


def test_frontmatter_markdown_paths_remain_metadata_not_relations(tmp_path: Path) -> None:
    _write(
        tmp_path / "a.md",
        "---\ntype: Node\nsource_path: source/SKILL.md\nmetadata:\n  related:\n    - /b.md\n---\n",
    )
    _write(tmp_path / "b.md", "---\ntype: Node\n---\n")

    bundle = load_bundle(tmp_path)

    assert bundle.links.count().execute() == 0
    assert bundle.validate() == []


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


def test_all_caps_markdown_is_a_concept_and_requires_type(tmp_path: Path) -> None:
    _write(tmp_path / "API.md", "# API\n")

    report = validate_path(tmp_path)

    assert not report.is_conformant
    assert report.concept_count == 0
    assert report.reserved_count == 0
    assert [item.code for item in report.violations] == ["OKF001"]


def test_logical_keys_do_not_depend_on_checkout_path(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write(first / "nested" / "concept.md", "---\ntype: Reference\n---\n# Concept\n")
    shutil.copytree(first, second)

    first_keys = load_bundle(first).concepts.select("logical_key").execute()
    second_keys = load_bundle(second).concepts.select("logical_key").execute()

    assert first_keys.to_dict(orient="records") == [{"logical_key": "nested/concept"}]
    assert second_keys.to_dict(orient="records") == first_keys.to_dict(orient="records")


def test_one_unparseable_concept_does_not_abort_the_run(tmp_path: Path) -> None:
    _write(tmp_path / "a-broken.md", "---\nblob: !!binary aGk=\n---\n")
    _write(tmp_path / "b-cyclic.md", "---\nself: &a\n  child: *a\n---\n")
    _write(tmp_path / "c-good.md", "---\ntype: Node\n---\n# Good\n")

    report = validate_path(tmp_path)

    assert report.markdown_count == 3
    assert report.concept_count == 1
    assert [item.code for item in report.violations] == ["OKF001", "OKF001"]


def test_malformed_url_in_frontmatter_does_not_abort_the_run(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Node\nrelated: http://[oops/x.md\n---\n")

    report = validate_path(tmp_path)

    assert report.is_conformant
    assert report.concept_count == 1


def test_prose_ending_in_md_is_not_a_link(tmp_path: Path) -> None:
    _write(
        tmp_path / "a.md",
        "---\ntype: Node\ndescription: Supersedes the old draft in notes.md\n---\n",
    )

    bundle = load_bundle(tmp_path)

    assert bundle.links.count().execute() == 0
    assert bundle.validate() == []


def test_fenced_code_does_not_create_log_date_headings(tmp_path: Path) -> None:
    _write(
        tmp_path / "log.md",
        "# Log\n\n## 2026-01-01\n\n```markdown\n## not a date heading\n```\n",
    )

    bundle = load_bundle(tmp_path)

    assert bundle.is_conformant
    assert bundle.validate() == []


def test_fenced_code_does_not_satisfy_the_index_title_rule(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "Intro paragraph.\n\n```markdown\n# fake title\n```\n")

    bundle = load_bundle(tmp_path)

    assert [item.code for item in bundle.validate()] == ["OKF005"]


def test_empty_level_one_heading_does_not_satisfy_the_index_title_rule(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "#\n\nIntro paragraph.\n")

    bundle = load_bundle(tmp_path)

    assert [item.code for item in bundle.validate()] == ["OKF005"]


def test_empty_level_one_heading_does_not_satisfy_the_log_title_rule(tmp_path: Path) -> None:
    _write(tmp_path / "log.md", "# \n\n## 2026-01-01\n\n* Entry\n")

    bundle = load_bundle(tmp_path)

    assert [item.code for item in bundle.validate()] == ["OKF007"]


def test_uppercase_markdown_extension_is_discovered_and_resolvable(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Node\n---\n[B](B.MD)\n")
    _write(tmp_path / "B.MD", "---\ntype: Node\n---\n# B\n")

    report = validate_path(tmp_path)
    bundle = load_bundle(tmp_path)

    assert report.markdown_count == 2
    assert report.concept_count == 2
    assert report.is_conformant
    [link] = bundle.links.execute().to_dict(orient="records")
    assert link["target_id"] == "B"
    assert link["exists"]


def test_graph_has_no_phantom_nodes_for_unparseable_targets(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Node\n---\n[B](b.md)\n")
    _write(tmp_path / "b.md", "no frontmatter\n")

    graph = load_bundle(tmp_path).to_networkx()

    assert set(graph.nodes) == {"a"}
    assert all("type" in attributes for _node, attributes in graph.nodes(data=True))


def test_graph_node_without_a_title_uses_none_not_nan(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Node\n---\n# A\n")

    graph = load_bundle(tmp_path).to_networkx()

    assert graph.nodes["a"]["title"] is None
