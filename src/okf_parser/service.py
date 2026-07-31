"""JSON-ready application services shared by CLI and MCP."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import networkx as nx

from okf_parser.bundle import load_bundle, validate_path
from okf_parser.duckdb import attach_okf
from okf_parser.formatting import FormatReport, format_path
from okf_parser.schema_export import export_json_schema, export_zod_schema

if TYPE_CHECKING:
    from collections.abc import Sequence


def check_bundle(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Validate every Markdown file below a path."""
    report = validate_path(Path(path), exclude)
    return {
        "root": str(report.root),
        "conformant": report.is_conformant,
        "markdown_count": report.markdown_count,
        "concept_count": report.concept_count,
        "reserved_count": report.reserved_count,
        "diagnostics": [item.model_dump(mode="json") for item in report.violations],
    }


def inventory_bundle(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Count concepts by their producer-defined type."""
    bundle = load_bundle(Path(path), exclude)
    rows = (
        bundle.concepts.group_by("concept_type")
        .aggregate(concept_count=lambda table: table.count())
        .order_by("concept_type")
        .execute()
        .to_dict(orient="records")
    )
    return {"root": str(bundle.root), "types": rows}


def graph_bundle(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Summarize the resolved concept graph."""
    bundle = load_bundle(Path(path), exclude)
    graph = bundle.to_networkx()
    return {
        "root": str(bundle.root),
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "weakly_connected_components": nx.number_weakly_connected_components(graph),
        "strongly_connected_components": nx.number_strongly_connected_components(graph),
        "directed_acyclic": nx.is_directed_acyclic_graph(graph),
    }


def schema_bundle(
    path: str,
    fmt: str = "json",
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
) -> dict[str, object] | str:
    """Export string-first JSON Schema or Zod definitions for Astro."""
    if fmt == "zod":
        return export_zod_schema(
            path,
            exclude,
            infer_types=infer_types,
            casts=casts,
        )
    return export_json_schema(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
    )


def _format_payload(report: FormatReport) -> dict[str, object]:
    return {
        "markdown_count": report.markdown_count,
        "clean": report.clean,
        "changed_paths": list(report.changed_paths),
        "skipped_paths": list(report.skipped_paths),
        "succeeded": report.succeeded,
        "written": report.written,
    }


def check_format(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Check mdformat canonical form without writing files."""
    return _format_payload(format_path(Path(path), exclude=exclude))


def write_format(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Explicitly rewrite Markdown files into mdformat canonical form."""
    return _format_payload(format_path(Path(path), write=True, exclude=exclude))


def export_duckdb(
    path: str,
    database: str,
    schema: str = "okf",
    *,
    overwrite: bool = False,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Materialize an OKF bundle into a DuckDB database file."""
    connection = duckdb.connect(database)
    try:
        result = attach_okf(
            connection,
            path,
            schema=schema,
            overwrite=overwrite,
            exclude=exclude,
        )
    finally:
        connection.close()
    return {**result, "database": str(Path(database).resolve())}
