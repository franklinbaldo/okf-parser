"""The bundle's concept graph: concepts as nodes, resolved links as edges.

The graph is defined once, by ``okf-engine``'s ``ConceptGraph``
(``okf-engine/src/graph.rs``): the binary computes the summary when it loads
the bundle, and this module only carries its answer.

No third-party graph library is required. ``BundleGraph.to_networkx`` bridges
to NetworkX for callers who want its algorithms, and imports it only then.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterable
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
    counts: GraphSummary

    @classmethod
    def from_records(
        cls,
        root: Path,
        nodes: Iterable[GraphNode],
        links: Iterable[GraphEdge],
        counts: GraphSummary,
    ) -> BundleGraph:
        """Keep every node and only the links whose endpoints are both nodes.

        A target that exists on disk but never became a concept - because it
        failed to parse, or is a reserved document - must not become an edge.
        """
        node_tuple = tuple(nodes)
        known = {node.concept_id for node in node_tuple}
        edges = tuple(link for link in links if link.source_id in known and link.target_id in known)
        return cls(root=root, nodes=node_tuple, edges=edges, counts=counts)

    def summary(self) -> GraphSummary:
        """Count nodes, edges, and components, and report whether the graph is a DAG."""
        return self.counts

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
