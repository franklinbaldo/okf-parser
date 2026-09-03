"""Deterministic graph navigation over one canonical relational snapshot."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, cast

import networkx as nx

if TYPE_CHECKING:
    from okf_parser.relational_read import CanonicalRelations

type RelationDirection = Literal["incoming", "outgoing", "both"]


class GraphNodeRecord(TypedDict):
    """Stable public shape for one concept returned by graph navigation."""

    concept_id: str
    path: str | None
    concept_type: str | None
    title: str | None
    depth: NotRequired[int]


class DegreeRecord(TypedDict):
    """Stable public shape for one concept's directed degree summary."""

    concept_id: str
    in_degree: int
    out_degree: int
    degree: int


class TopologyResult(TypedDict):
    """Stable public shape for deterministic bundle topology."""

    nodes: int
    edges: int
    directed_acyclic: bool
    roots: list[str]
    leaves: list[str]
    isolated: list[str]
    weak_components: list[list[str]]
    strong_components: list[list[str]]
    degree_ranking: list[DegreeRecord]


class ConceptResolutionError(ValueError):
    """Report a concept reference that cannot be resolved in one snapshot."""


def _resolve_graph_reference(graph: nx.MultiDiGraph, reference: str) -> str:
    if graph.has_node(reference):
        return reference
    matches = sorted(
        concept_id
        for concept_id, attributes in graph.nodes(data=True)
        if attributes.get("path") == reference
    )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        message = f'concept reference "{reference}" does not exist in this snapshot'
        raise ConceptResolutionError(message)
    message = f'concept reference "{reference}" is ambiguous: {matches}'
    raise ConceptResolutionError(message)


def resolve_concept_id(relations: CanonicalRelations, reference: str) -> str:
    """Resolve an exact canonical concept id or exact authored Markdown path."""
    return _resolve_graph_reference(relations.to_networkx(), reference)


def _node_record(
    graph: nx.MultiDiGraph,
    concept_id: str,
    *,
    depth: int | None = None,
) -> GraphNodeRecord:
    attributes = graph.nodes[concept_id]
    record = GraphNodeRecord(
        concept_id=concept_id,
        path=cast("str | None", attributes.get("path")),
        concept_type=cast("str | None", attributes.get("type")),
        title=cast("str | None", attributes.get("title")),
    )
    if depth is not None:
        record["depth"] = depth
    return record


def _neighbor_ids(
    graph: nx.MultiDiGraph,
    concept_id: str,
    direction: RelationDirection,
) -> list[str]:
    if direction == "incoming":
        return sorted(graph.predecessors(concept_id))
    if direction == "outgoing":
        return sorted(graph.successors(concept_id))
    return sorted({*graph.predecessors(concept_id), *graph.successors(concept_id)})


def related(
    relations: CanonicalRelations,
    reference: str,
    *,
    direction: RelationDirection = "both",
) -> list[GraphNodeRecord]:
    """Return deterministic immediate neighbors of one concept."""
    graph = relations.to_networkx()
    concept_id = _resolve_graph_reference(graph, reference)
    return [_node_record(graph, neighbor) for neighbor in _neighbor_ids(graph, concept_id, direction)]


def reachability(
    relations: CanonicalRelations,
    reference: str,
    *,
    direction: RelationDirection = "outgoing",
    max_depth: int | None = None,
) -> list[GraphNodeRecord]:
    """Return cycle-safe deterministic reachability from one concept.

    The seed is omitted. Results are ordered by minimum depth and canonical
    concept id. ``max_depth=None`` traverses the whole reachable component.
    """
    if max_depth is not None and max_depth < 1:
        message = "max_depth must be at least 1 when provided"
        raise ValueError(message)

    graph = relations.to_networkx()
    seed = _resolve_graph_reference(graph, reference)
    depths: dict[str, int] = {seed: 0}
    queue: deque[str] = deque([seed])

    while queue:
        current = queue.popleft()
        current_depth = depths[current]
        if max_depth is not None and current_depth >= max_depth:
            continue
        for neighbor in _neighbor_ids(graph, current, direction):
            if neighbor in depths:
                continue
            depths[neighbor] = current_depth + 1
            queue.append(neighbor)

    ordered = sorted(
        ((concept_id, depth) for concept_id, depth in depths.items() if concept_id != seed),
        key=lambda item: (item[1], item[0]),
    )
    return [_node_record(graph, concept_id, depth=depth) for concept_id, depth in ordered]


def shortest_path(
    relations: CanonicalRelations,
    source: str,
    target: str,
    *,
    direction: RelationDirection = "outgoing",
) -> list[GraphNodeRecord]:
    """Return one deterministic shortest path between two concepts."""
    graph = relations.to_networkx()
    source_id = _resolve_graph_reference(graph, source)
    target_id = _resolve_graph_reference(graph, target)
    if source_id == target_id:
        return [_node_record(graph, source_id, depth=0)]

    parent: dict[str, str | None] = {source_id: None}
    queue: deque[str] = deque([source_id])
    while queue and target_id not in parent:
        current = queue.popleft()
        for neighbor in _neighbor_ids(graph, current, direction):
            if neighbor in parent:
                continue
            parent[neighbor] = current
            queue.append(neighbor)

    if target_id not in parent:
        message = f'no {direction} path from "{source_id}" to "{target_id}"'
        raise nx.NetworkXNoPath(message)

    ids: list[str] = []
    current: str | None = target_id
    while current is not None:
        ids.append(current)
        current = parent[current]
    ids.reverse()
    return [_node_record(graph, concept_id, depth=depth) for depth, concept_id in enumerate(ids)]


def _degree_row(graph: nx.MultiDiGraph, concept_id: str) -> tuple[int, str, int, int]:
    in_degree = cast("int", graph.in_degree(concept_id))
    out_degree = cast("int", graph.out_degree(concept_id))
    return (in_degree + out_degree, concept_id, in_degree, out_degree)


def topology(relations: CanonicalRelations) -> TopologyResult:
    """Return deterministic query-oriented topology for the canonical graph."""
    graph = relations.to_networkx()
    roots = sorted(node for node, degree in graph.in_degree() if degree == 0)
    leaves = sorted(node for node, degree in graph.out_degree() if degree == 0)
    isolated = sorted(nx.isolates(graph))
    weak_components = sorted(
        (sorted(component) for component in nx.weakly_connected_components(graph)),
        key=lambda component: (component[0] if component else "", len(component), component),
    )
    strong_components = sorted(
        (sorted(component) for component in nx.strongly_connected_components(graph)),
        key=lambda component: (component[0] if component else "", len(component), component),
    )
    ranked = sorted(
        (_degree_row(graph, node) for node in graph.nodes),
        key=lambda item: (-item[0], item[1]),
    )
    degree_ranking = [
        DegreeRecord(
            concept_id=concept_id,
            in_degree=in_degree,
            out_degree=out_degree,
            degree=degree,
        )
        for degree, concept_id, in_degree, out_degree in ranked
    ]
    return TopologyResult(
        nodes=graph.number_of_nodes(),
        edges=graph.number_of_edges(),
        directed_acyclic=nx.is_directed_acyclic_graph(graph),
        roots=roots,
        leaves=leaves,
        isolated=isolated,
        weak_components=weak_components,
        strong_components=strong_components,
        degree_ranking=degree_ranking,
    )
