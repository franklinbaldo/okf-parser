"""The bundle's concept graph, independent of any third-party graph library."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser import load_bundle
from okf_parser.graph import GraphEdge, GraphNode, GraphSummary

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _node(root: Path, name: str, *links: str) -> None:
    body = "".join(f"[{target}]({target}.md)\n" for target in links)
    _write(root / f"{name}.md", f"---\ntype: Node\n---\n{body}")


def test_graph_nodes_are_concepts_with_their_attributes(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Node\ntitle: Alpha\n---\n")

    graph = load_bundle(tmp_path).graph()

    assert graph.nodes == (GraphNode(concept_id="a", path="a.md", type="Node", title="Alpha"),)


def test_graph_edges_are_links_that_resolved_to_concepts(tmp_path: Path) -> None:
    _node(tmp_path, "a", "b", "missing")
    _node(tmp_path, "b")

    graph = load_bundle(tmp_path).graph()

    assert graph.edges == (
        GraphEdge(source_id="a", target_id="b", raw_target="b.md", origin="body"),
    )


def test_graph_ignores_links_to_reserved_and_unparseable_documents(tmp_path: Path) -> None:
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(tmp_path / "broken.md", "no frontmatter\n")
    _write(tmp_path / "a.md", "---\ntype: Node\n---\n[I](index.md) [B](broken.md)\n")

    graph = load_bundle(tmp_path).graph()

    assert [node.concept_id for node in graph.nodes] == ["a"]
    assert graph.edges == ()


def test_summary_of_a_chain_and_an_isolated_concept(tmp_path: Path) -> None:
    _node(tmp_path, "a", "b")
    _node(tmp_path, "b", "c")
    _node(tmp_path, "c")
    _node(tmp_path, "d")

    summary = load_bundle(tmp_path).graph().summary()

    assert summary == GraphSummary(
        nodes=4,
        edges=2,
        weakly_connected_components=2,
        strongly_connected_components=4,
        directed_acyclic=True,
    )


def test_summary_collapses_a_cycle_into_one_strong_component(tmp_path: Path) -> None:
    _node(tmp_path, "a", "b")
    _node(tmp_path, "b", "c")
    _node(tmp_path, "c", "a")

    summary = load_bundle(tmp_path).graph().summary()

    assert summary.strongly_connected_components == 1
    assert summary.weakly_connected_components == 1
    assert not summary.directed_acyclic


def test_summary_treats_a_self_link_as_a_cycle(tmp_path: Path) -> None:
    _node(tmp_path, "a", "a")

    summary = load_bundle(tmp_path).graph().summary()

    assert summary.edges == 1
    assert summary.strongly_connected_components == 1
    assert not summary.directed_acyclic


def test_summary_counts_parallel_links_as_distinct_edges(tmp_path: Path) -> None:
    _node(tmp_path, "a", "b", "b")
    _node(tmp_path, "b")

    assert load_bundle(tmp_path).graph().summary().edges == 2


def test_summary_of_an_empty_bundle(tmp_path: Path) -> None:
    summary = load_bundle(tmp_path).graph().summary()

    assert summary == GraphSummary(
        nodes=0,
        edges=0,
        weakly_connected_components=0,
        strongly_connected_components=0,
        directed_acyclic=True,
    )


def test_summary_handles_a_long_cycle_without_recursion(tmp_path: Path) -> None:
    size = 3000
    for index in range(size):
        _node(tmp_path, f"n{index}", f"n{(index + 1) % size}")

    summary = load_bundle(tmp_path).graph().summary()

    assert summary.strongly_connected_components == 1


def test_to_networkx_projects_the_same_nodes_and_edges(tmp_path: Path) -> None:
    pytest.importorskip("networkx")
    _write(tmp_path / "a.md", "---\ntype: Node\n---\n[B](b.md)\n")
    _write(tmp_path / "b.md", "---\ntype: Node\n---\n")

    graph = load_bundle(tmp_path).graph().to_networkx()

    assert set(graph.nodes) == {"a", "b"}
    assert graph.nodes["a"] == {"path": "a.md", "type": "Node", "title": None}
    assert list(graph.edges(data=True)) == [("a", "b", {"raw_target": "b.md", "origin": "body"})]
    assert graph.graph["bundle_root"] == str(tmp_path.resolve())


def test_bundle_to_networkx_is_deprecated_in_favor_of_graph(tmp_path: Path) -> None:
    pytest.importorskip("networkx")
    _node(tmp_path, "a")

    with pytest.deprecated_call(match=r"bundle\.graph\(\)\.to_networkx\(\)"):
        graph = load_bundle(tmp_path).to_networkx()

    assert set(graph.nodes) == {"a"}
