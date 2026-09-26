"""The bundle's concept graph: concepts as nodes, resolved links as edges.

This mirrors ``okf-engine``'s ``ConceptGraph`` (``okf-engine/src/graph.rs``),
which answers the MCP ``graph`` tool natively. The two must agree;
``tests/test_mcp.py`` holds them to it through the served tool.

No third-party graph library is required. ``BundleGraph.to_networkx`` bridges
to NetworkX for callers who want its algorithms, and imports it only then.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    import networkx as nx


class GraphNode(BaseModel):
    """One concept, carrying the attributes a graph consumer needs."""

    model_config = ConfigDict(frozen=True)

    concept_id: str
    path: str
    type: str
    title: str | None


class GraphEdge(BaseModel):
    """One Markdown link whose target resolved to a concept."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    target_id: str
    raw_target: str
    origin: str


class GraphSummary(BaseModel):
    """Structural counts over the concept graph."""

    model_config = ConfigDict(frozen=True)

    nodes: int
    edges: int
    weakly_connected_components: int
    strongly_connected_components: int
    directed_acyclic: bool


class NetworkXUnavailableError(ImportError):
    """``to_networkx`` was called without the optional NetworkX dependency."""

    def __init__(self) -> None:
        """Name the extra that provides NetworkX."""
        super().__init__("to_networkx() needs NetworkX: install 'okf-parser[networkx]'")


@dataclass(frozen=True, slots=True)
class BundleGraph:
    """A directed multigraph over one bundle's concepts."""

    root: Path
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    @classmethod
    def from_records(
        cls, root: Path, nodes: Iterable[GraphNode], links: Iterable[GraphEdge]
    ) -> BundleGraph:
        """Keep every node and only the links whose endpoints are both nodes.

        A target that exists on disk but never became a concept - because it
        failed to parse, or is a reserved document - must not become an edge.
        """
        node_tuple = tuple(nodes)
        known = {node.concept_id for node in node_tuple}
        edges = tuple(link for link in links if link.source_id in known and link.target_id in known)
        return cls(root=root, nodes=node_tuple, edges=edges)

    def summary(self) -> GraphSummary:
        """Count nodes, edges, and components, and report whether the graph is a DAG."""
        successors = self._successors()
        strong = _strongly_connected_components(
            [node.concept_id for node in self.nodes], successors
        )
        self_linked = any(edge.source_id == edge.target_id for edge in self.edges)
        return GraphSummary(
            nodes=len(self.nodes),
            edges=len(self.edges),
            weakly_connected_components=self._weak_component_count(),
            strongly_connected_components=len(strong),
            directed_acyclic=not self_linked and all(len(part) == 1 for part in strong),
        )

    def to_networkx(self) -> nx.MultiDiGraph:
        """Project into a NetworkX ``MultiDiGraph`` (needs ``okf-parser[networkx]``)."""
        try:
            import networkx as nx  # noqa: PLC0415 - optional dependency, imported on demand
        except ModuleNotFoundError as exc:
            raise NetworkXUnavailableError from exc
        graph = nx.MultiDiGraph(bundle_root=str(self.root))
        for node in self.nodes:
            graph.add_node(node.concept_id, path=node.path, type=node.type, title=node.title)
        for edge in self.edges:
            graph.add_edge(
                edge.source_id, edge.target_id, raw_target=edge.raw_target, origin=edge.origin
            )
        return graph

    def _successors(self) -> dict[str, list[str]]:
        successors: dict[str, list[str]] = defaultdict(list)
        for edge in self.edges:
            successors[edge.source_id].append(edge.target_id)
        return successors

    def _weak_component_count(self) -> int:
        parent = {node.concept_id: node.concept_id for node in self.nodes}

        def find(item: str) -> str:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        for edge in self.edges:
            parent[find(edge.source_id)] = find(edge.target_id)
        return len({find(item) for item in parent})


def _strongly_connected_components(
    nodes: list[str], successors: Mapping[str, list[str]]
) -> list[list[str]]:
    """Tarjan's algorithm, iterative so a long cycle cannot exhaust the stack."""
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []

    for start in nodes:
        if start in index:
            continue
        work = [(start, iter(successors.get(start, ())))]
        index[start] = lowlink[start] = len(index)
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is None:
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[node])
                if lowlink[node] == index[node]:
                    components.append(_pop_component(node, stack, on_stack))
                continue
            if child not in index:
                index[child] = lowlink[child] = len(index)
                stack.append(child)
                on_stack.add(child)
                work.append((child, iter(successors.get(child, ()))))
            elif child in on_stack:
                lowlink[node] = min(lowlink[node], index[child])
    return components


def _pop_component(root: str, stack: list[str], on_stack: set[str]) -> list[str]:
    """Pop one finished strongly connected component off Tarjan's stack."""
    component: list[str] = []
    while True:
        member = stack.pop()
        on_stack.discard(member)
        component.append(member)
        if member == root:
            return component
