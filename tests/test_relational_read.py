"""Tests for the canonical relational read-service boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser import PortableRelationProvider, open_relations
from okf_parser.bundle import Bundle, load_bundle

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class RecordingProvider:
    """Small test provider proving consumers can reuse the public boundary."""

    name = "recording"

    def __init__(self) -> None:
        """Initialize an empty provider call log."""
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def load(self, root: Path, exclude: Sequence[str]) -> Bundle:
        """Record the request and delegate to the canonical bundle loader."""
        normalized_exclude = tuple(exclude)
        self.calls.append((root, normalized_exclude))
        return load_bundle(root, normalized_exclude)


def _write_bundle(root: Path) -> None:
    (root / "a.md").write_text(
        "---\ntype: Note\ntitle: A\n---\n\nSee [B](b.md).\n",
        encoding="utf-8",
    )
    (root / "b.md").write_text(
        "---\ntype: Note\ntitle: B\n---\n\nLeaf.\n",
        encoding="utf-8",
    )
    (root / "broken.md").write_text("---\ntitle: Broken\n---\n\nNo type.\n", encoding="utf-8")


def test_open_relations_exposes_one_coherent_canonical_snapshot(tmp_path: Path) -> None:
    _write_bundle(tmp_path)

    relations = open_relations(tmp_path)

    assert relations.provider == "portable"
    assert relations.root == tmp_path.resolve()
    # Structurally invalid concepts remain part of the canonical snapshot.
    # Validation reports the defect separately instead of silently deleting data.
    assert relations.concepts.count().execute() == 3
    assert relations.links.count().execute() == 1
    assert relations.reserved.count().execute() == 0

    diagnostics = relations.diagnostics.execute().to_dict(orient="records")
    assert diagnostics == [
        {
            "code": "OKF002",
            "severity": "error",
            "path": "broken.md",
            "message": "frontmatter must contain a non-empty string type",
        }
    ]

    graph = relations.to_networkx()
    assert sorted(graph.nodes) == ["a", "b", "broken"]
    assert graph.nodes["broken"]["type"] == ""
    assert list(graph.edges()) == [("a", "b")]


def test_open_relations_accepts_a_provider_without_changing_consumer_contract(
    tmp_path: Path,
) -> None:
    _write_bundle(tmp_path)
    provider = RecordingProvider()

    relations = open_relations(tmp_path, exclude=("broken.md",), provider=provider)

    assert relations.provider == "recording"
    assert provider.calls == [(tmp_path.resolve(), ("broken.md",))]
    assert relations.concepts.count().execute() == 2
    assert relations.diagnostics.count().execute() == 0


def test_portable_provider_is_public_and_directly_reusable(tmp_path: Path) -> None:
    _write_bundle(tmp_path)
    provider = PortableRelationProvider()

    bundle = provider.load(tmp_path.resolve(), ("broken.md",))

    assert provider.name == "portable"
    assert bundle.concepts.count().execute() == 2
