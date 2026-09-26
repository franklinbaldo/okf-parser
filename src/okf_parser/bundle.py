"""Load an OKF bundle through the native binary and validate its structure.

Ingestion is the binary's job (RFC 0024): ``load_bundle`` asks it for the
bundle's records and holds them as immutable, typed tuples.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from okf_parser.graph import BundleGraph, GraphEdge, GraphNode, GraphSummary
from okf_parser.models import (
    ConceptRecord,
    LinkRecord,
    ReservedRecord,
    Severity,
    ValidationReport,
    Violation,
)
from okf_parser.relational_schema import validate_relations
from okf_parser.rust_core import native_binary, rust_load_bundle
from okf_parser.type_specs import missing_type_specs, required_type_spec_fields
from okf_parser.typed_relations import TypedRelations, compile_bundle_types

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    import networkx as nx


def _ordered(diagnostics: Iterable[Violation]) -> list[Violation]:
    """Order diagnostics deterministically by path, severity, code and message."""
    return sorted(
        diagnostics,
        key=lambda item: (item.path, item.severity.value, item.code, item.message),
    )


@dataclass(frozen=True, slots=True)
class Bundle:
    """An immutable view of one OKF bundle, as the native engine loaded it.

    Deliberately not a Pydantic model: ``validate`` would collide with
    ``BaseModel.validate``. Its fields are validated models already.
    """

    root: Path
    concepts: tuple[ConceptRecord, ...]
    reserved: tuple[ReservedRecord, ...]
    links: tuple[LinkRecord, ...]
    diagnostics: tuple[Violation, ...]
    markdown_count: int
    graph_summary: GraphSummary

    def validate(self) -> list[Violation]:
        """Return deterministic diagnostics ordered by path, severity, and code."""
        return _ordered(self.diagnostics)

    @property
    def concept_types(self) -> set[str]:
        """Every producer-defined type observed in the bundle."""
        return {concept.concept_type for concept in self.concepts if concept.concept_type}

    def compile_types(self, spec_template: str | None = None) -> TypedRelations:
        """Compile declared concept types into live in-process relations."""
        return compile_bundle_types(self, spec_template)

    @property
    def is_conformant(self) -> bool:
        """Whether the bundle has no normative errors."""
        return not any(item.severity is Severity.ERROR for item in self.diagnostics)

    def graph(self) -> BundleGraph:
        """Project concepts and resolved Markdown links into a directed multigraph."""
        nodes = (
            GraphNode(
                concept_id=concept.concept_id,
                path=concept.path,
                type=concept.concept_type,
                title=concept.title,
            )
            for concept in self.concepts
        )
        edges = (
            GraphEdge(
                source_id=link.source_id,
                target_id=link.target_id,
                raw_target=link.raw_target,
                origin=link.origin,
            )
            for link in self.links
            if link.target_id is not None
        )
        return BundleGraph.from_records(self.root, nodes, edges, self.graph_summary)

    def to_networkx(self) -> nx.MultiDiGraph:
        """Deprecated alias of ``bundle.graph().to_networkx()``."""
        warnings.warn(
            "Bundle.to_networkx() is deprecated; use bundle.graph().to_networkx()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.graph().to_networkx()


def load_bundle(
    root: Path,
    exclude: Sequence[str] = (),
    *,
    rust_core: Path | None = None,
) -> Bundle:
    """Load a bundle through the native binary.

    Exclusions come from the bundle's ``.okfignore`` and from ``exclude``,
    which a caller supplies for a one-off run. ``rust_core`` pins a specific
    binary; by default the one installed with this package is used.
    """
    root = root.resolve()
    if not root.is_dir():
        msg = f"bundle root is not a directory: {root}"
        raise NotADirectoryError(msg)
    loaded = rust_load_bundle(root, native_binary(rust_core), exclude)
    return Bundle(
        root=Path(loaded.root),
        concepts=loaded.concepts,
        reserved=loaded.reserved,
        links=loaded.links,
        diagnostics=loaded.diagnostics,
        markdown_count=loaded.markdown_count,
        graph_summary=loaded.graph,
    )


def validate_path(
    path: Path,
    exclude: Sequence[str] = (),
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
    relational_schema: Path | None = None,
) -> ValidationReport:
    """Validate every Markdown file recursively below a path as OKF v0.2.

    ``require_spec`` adds the optional rule that every producer-defined type in
    use has a specification document at the path its template derives.
    """
    bundle = load_bundle(path, exclude)
    diagnostics = list(bundle.diagnostics)
    if relational_schema is not None:
        schema_path = (
            relational_schema
            if relational_schema.is_absolute()
            else bundle.root / relational_schema
        )
        diagnostics.extend(validate_relations(bundle, schema_path))
    if require_spec is not None:
        diagnostics.extend(
            missing_type_specs(
                bundle.root,
                bundle.concept_types,
                require_spec,
                normative=normative_spec,
            )
        )
        diagnostics.extend(
            required_type_spec_fields(
                bundle.root,
                [concept.model_dump() for concept in bundle.concepts],
                require_spec,
                normative=normative_spec,
            )
        )
    return ValidationReport(
        root=bundle.root,
        markdown_count=bundle.markdown_count,
        concept_count=len(bundle.concepts),
        reserved_count=len(bundle.reserved),
        violations=tuple(_ordered(diagnostics)),
    )
