"""Explain how Markdown paths participated in one strict OKF bundle check."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.bundle import load_bundle
from okf_parser.discovery import discover_markdown

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def classify_path(path: Path, exclude: Sequence[str] = ()) -> dict[str, list[str]]:
    """Classify candidate Markdown as concept, reserved, ignored, or invalid.

    This is intentionally explanatory rather than a second conformance mode.
    `load_bundle` remains the authority for active bundle semantics. A second
    unfiltered discovery is performed only when classification is explicitly
    requested so the normal `check` path keeps its existing traversal cost.
    """
    root = path.resolve()
    bundle = load_bundle(root, exclude)

    concept_rows = bundle.concepts.select("path", "concept_type").execute().to_dict(
        orient="records"
    )
    all_concepts = {row["path"] for row in concept_rows if isinstance(row["path"], str)}
    concepts = {
        row["path"]
        for row in concept_rows
        if isinstance(row["path"], str)
        and isinstance(row["concept_type"], str)
        and row["concept_type"]
    }
    reserved = {
        row["path"]
        for row in bundle.reserved.select("path").execute().to_dict(orient="records")
        if isinstance(row["path"], str)
    }
    diagnostic_paths = {item.path for item in bundle.diagnostics}
    active = all_concepts | reserved | diagnostic_paths

    candidates = {
        candidate.relative_to(root).as_posix() for candidate in discover_markdown(root)
    }
    return {
        "concepts": sorted(concepts),
        "reserved": sorted(reserved),
        "ignored": sorted(candidates - active),
        "invalid_or_untyped": sorted(active - concepts - reserved),
    }
