"""JSON-ready application services shared by CLI and MCP."""

from __future__ import annotations

from pathlib import Path

import duckdb
import networkx as nx

from okf_tools.bundle import load_bundle, validate_path
from okf_tools.duckdb import attach_okf
from okf_tools.formatting import format_path


def check_bundle(
    path: str,
    *,
    require_all_caps_frontmatter: bool = False,
) -> dict[str, object]:
    """Validate every Markdown file below a path."""
    report = validate_path(
        Path(path),
        require_all_caps_frontmatter=require_all_caps_frontmatter,
    )
    return {
        "root": str(report.root),
        "conformant": report.is_conformant,
        "markdown_count": report.markdown_count,
        "concept_count": report.concept_count,
        "reserved_count": report.reserved_count,
        "diagnostics": [
            {
                "code": item.code,
                "severity": item.severity.value,
                "path": item.path,
                "message": item.message,
            }
            for item in report.violations
        ],
    }


def inventory_bundle(path: str) -> dict[str, object]:
    """Count concepts by their producer-defined type."""
    bundle = load_bundle(Path(path))
    rows = (
        bundle.concepts.group_by("concept_type")
        .aggregate(concept_count=lambda table: table.count())
        .order_by("concept_type")
        .execute()
        .to_dict(orient="records")
    )
    return {"root": str(bundle.root), "types": rows}


def graph_bundle(path: str) -> dict[str, object]:
    """Summarize the resolved concept graph."""
    bundle = load_bundle(Path(path))
    graph = bundle.to_networkx()
    return {
        "root": str(bundle.root),
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "weakly_connected_components": nx.number_weakly_connected_components(graph),
        "strongly_connected_components": nx.number_strongly_connected_components(graph),
        "directed_acyclic": nx.is_directed_acyclic_graph(graph),
    }


def check_format(path: str) -> dict[str, object]:
    """Check mdformat canonical form without writing files."""
    report = format_path(Path(path))
    return {
        "markdown_count": report.markdown_count,
        "clean": report.clean,
        "changed_paths": list(report.changed_paths),
        "written": report.written,
    }


def write_format(path: str) -> dict[str, object]:
    """Explicitly rewrite Markdown files into mdformat canonical form."""
    report = format_path(Path(path), write=True)
    return {
        "markdown_count": report.markdown_count,
        "clean": report.clean,
        "changed_paths": list(report.changed_paths),
        "written": report.written,
    }


def export_duckdb(path: str, database: str, schema: str = "okf") -> dict[str, object]:
    """Materialize an OKF bundle into a DuckDB database file."""
    connection = duckdb.connect(database)
    try:
        result = attach_okf(connection, path, schema=schema)
    finally:
        connection.close()
    return {**result, "database": str(Path(database).resolve())}
