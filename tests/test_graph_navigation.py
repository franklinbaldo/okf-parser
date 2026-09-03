"""Tests for deterministic graph navigation over canonical relations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx
import pytest

from okf_parser import open_relations
from okf_parser.graph_navigation import (
    ConceptResolutionError,
    reachability,
    related,
    resolve_concept_id,
    shortest_path,
    topology,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write(root: Path, name: str, body: str) -> None:
    (root / f"{name}.md").write_text(
        f"---\ntype: Note\ntitle: {name.upper()}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _write_graph(root: Path) -> None:
    _write(root, "a", "[B](b.md) [C](c.md)")
    _write(root, "b", "[D](d.md)")
    _write(root, "c", "[D](d.md)")
    _write(root, "d", "[B](b.md)")
    _write(root, "z", "isolated")


def test_resolve_concept_id_accepts_canonical_id_or_path(tmp_path: Path) -> None:
    _write_graph(tmp_path)
    relations = open_relations(tmp_path)

    assert resolve_concept_id(relations, "a") == "a"
    assert resolve_concept_id(relations, "a.md") == "a"

    with pytest.raises(ConceptResolutionError, match="does not exist"):
        resolve_concept_id(relations, "missing")


def test_related_and_reachability_are_directional_and_cycle_safe(tmp_path: Path) -> None:
    _write_graph(tmp_path)
    relations = open_relations(tmp_path)

    outgoing = related(relations, "a", direction="outgoing")
    incoming = related(relations, "d", direction="incoming")

    assert [row["concept_id"] for row in outgoing] == ["b", "c"]
    assert [row["concept_id"] for row in incoming] == ["b", "c"]

    reachable = reachability(relations, "a", direction="outgoing")
    assert [(row["concept_id"], row["depth"]) for row in reachable] == [
        ("b", 1),
        ("c", 1),
        ("d", 2),
    ]

    reverse = reachability(relations, "d", direction="incoming", max_depth=1)
    assert [(row["concept_id"], row["depth"]) for row in reverse] == [
        ("b", 1),
        ("c", 1),
    ]


def test_shortest_path_uses_deterministic_neighbor_order(tmp_path: Path) -> None:
    _write_graph(tmp_path)
    relations = open_relations(tmp_path)

    path = shortest_path(relations, "a", "d")
    assert [row["concept_id"] for row in path] == ["a", "b", "d"]

    with pytest.raises(nx.NetworkXNoPath):
        shortest_path(relations, "z", "a")


def test_topology_exposes_roots_leaves_isolates_components_and_hubs(tmp_path: Path) -> None:
    _write_graph(tmp_path)
    relations = open_relations(tmp_path)

    result = topology(relations)

    assert result["directed_acyclic"] is False
    assert result["roots"] == ["a", "z"]
    assert result["leaves"] == ["z"]
    assert result["isolated"] == ["z"]
    assert result["weak_components"] == [["a", "b", "c", "d"], ["z"]]
    assert ["b", "d"] in result["strong_components"]

    ranking = result["degree_ranking"]
    assert ranking[0]["concept_id"] in {"b", "d"}
    assert ranking[-1] == {
        "concept_id": "z",
        "in_degree": 0,
        "out_degree": 0,
        "degree": 0,
    }
