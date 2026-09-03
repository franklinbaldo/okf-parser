"""Canonical relation-provider boundary for read-only OKF consumers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import ibis

from okf_parser.bundle import load_bundle

if TYPE_CHECKING:
    from collections.abc import Sequence

    import networkx as nx
    from ibis.expr.types import Table

    from okf_parser.bundle import Bundle
    from okf_parser.typed_relations import TypedRelations

_DIAGNOSTIC_SCHEMA = ibis.schema(
    {
        "code": "string",
        "severity": "string",
        "path": "string",
        "message": "string",
    }
)


class RelationProvider(Protocol):
    """Load one coherent bundle snapshot for the canonical read service."""

    @property
    def name(self) -> str:
        """Return the stable diagnostic name of this provider."""
        ...

    def load(self, root: Path, exclude: Sequence[str]) -> Bundle:
        """Load canonical relations for one root and exclusion policy."""
        ...


@dataclass(frozen=True, slots=True)
class PortableRelationProvider:
    """Portable provider backed by the canonical Python bundle loader."""

    name: str = "portable"

    def load(self, root: Path, exclude: Sequence[str]) -> Bundle:
        """Load one bundle without introducing a second parsing path."""
        return load_bundle(root, exclude)


@dataclass(frozen=True, slots=True)
class CanonicalRelations:
    """One coherent read snapshot shared by relational and graph consumers."""

    root: Path
    concepts: Table
    links: Table
    reserved: Table
    diagnostics: Table
    provider: str
    _bundle: Bundle = field(repr=False, compare=False)

    def compile_types(self, spec_template: str | None = None) -> TypedRelations:
        """Compile producer-declared typed relations from this same bundle snapshot."""
        return self._bundle.compile_types(spec_template)

    def to_networkx(self) -> nx.MultiDiGraph:
        """Project this same snapshot into the existing NetworkX graph model."""
        return self._bundle.to_networkx()


def _diagnostics_relation(bundle: Bundle) -> Table:
    rows = [item.model_dump(mode="json") for item in bundle.validate()]
    return ibis.memtable(rows, schema=_DIAGNOSTIC_SCHEMA)


def open_relations(
    root: str | Path,
    exclude: Sequence[str] = (),
    *,
    provider: RelationProvider | None = None,
) -> CanonicalRelations:
    """Open the four canonical read relations through one provider boundary.

    Consumers receive concepts, links, reserved documents and diagnostics from
    one coherent bundle load. Provider selection remains an implementation and
    deployment concern; callers operate on the same public relation contract.
    """
    resolved_root = Path(root).resolve()
    active_provider = provider or PortableRelationProvider()
    bundle = active_provider.load(resolved_root, exclude)
    return CanonicalRelations(
        root=bundle.root,
        concepts=bundle.concepts,
        links=bundle.links,
        reserved=bundle.reserved,
        diagnostics=_diagnostics_relation(bundle),
        provider=active_provider.name,
        _bundle=bundle,
    )
