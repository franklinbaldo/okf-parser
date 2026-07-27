"""Expose okf-parser through Cyclopts and FastMCP."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Literal

from cyclopts import App
from fastmcp import FastMCP

from okf_parser.duckdb import BundleExportError
from okf_parser.service import (
    check_bundle,
    check_format,
    export_duckdb,
    graph_bundle,
    inventory_bundle,
    write_format,
)

type McpTransport = Literal["stdio", "http", "sse"]


@dataclass(frozen=True, slots=True)
class CliResult:
    """A JSON payload and its intended process exit code."""

    payload: dict[str, object]
    exit_code: int = 0


def _render_cli_result(result: object) -> None:
    """Render stable JSON and preserve command-specific exit status."""
    if not isinstance(result, CliResult):
        return
    sys.stdout.write(
        json.dumps(result.payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    if result.exit_code:
        raise SystemExit(result.exit_code)


app = App(
    name="okf-parser",
    help="Validate and inspect OKF bundles with Ibis and NetworkX.",
    result_action=_render_cli_result,
)
mcp = FastMCP(
    name="okf-parser",
    instructions=(
        "Deterministic tools for validating and inspecting Open Knowledge Format bundles. "
        "Formatting checks are read-only; no tool exposed here rewrites files."
    ),
)


@app.command
def check(path: str) -> CliResult:
    """Validate every Markdown file recursively as OKF v0.2."""
    payload = check_bundle(path)
    return CliResult(payload, 0 if payload["conformant"] else 1)


@app.command
def inventory(path: str) -> CliResult:
    """Count concepts by type using an Ibis relation."""
    return CliResult(inventory_bundle(path))


@app.command
def graph(path: str) -> CliResult:
    """Summarize the resolved concept graph with NetworkX."""
    return CliResult(graph_bundle(path))


@app.command(name="format")
def format_command(path: str, *, write: bool = False) -> CliResult:
    """Check mdformat style, writing only when --write is explicit."""
    payload = write_format(path) if write else check_format(path)
    return CliResult(payload, 0 if payload["succeeded"] else 1)


@app.command(name="duckdb")
def duckdb_command(
    path: str,
    database: str = "okf.duckdb",
    schema: str = "okf",
    *,
    overwrite: bool = False,
) -> CliResult:
    """Materialize bundle relations into a DuckDB database."""
    try:
        return CliResult(export_duckdb(path, database, schema, overwrite=overwrite))
    except BundleExportError as exc:
        return CliResult(
            {
                "error": str(exc),
                "schema": exc.schema_name,
                "existing_tables": list(exc.tables),
            },
            exit_code=1,
        )


@app.command
def serve(
    transport: McpTransport = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Serve read-only inspection tools through MCP."""
    if transport == "stdio":
        mcp.run()
        return
    mcp.run(transport=transport, host=host, port=port)


@mcp.tool(name="check")
def mcp_check(path: str) -> dict[str, object]:
    """Validate every Markdown file recursively as OKF v0.2."""
    return check_bundle(path)


@mcp.tool(name="inventory")
def mcp_inventory(path: str) -> dict[str, object]:
    """Count concepts by type."""
    return inventory_bundle(path)


@mcp.tool(name="graph")
def mcp_graph(path: str) -> dict[str, object]:
    """Summarize resolved concept relationships."""
    return graph_bundle(path)


@mcp.tool(name="format_check")
def mcp_format_check(path: str) -> dict[str, object]:
    """Check mdformat style without modifying files."""
    return check_format(path)


def run_mcp_stdio() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


def main() -> None:
    """Run the Cyclopts command-line application."""
    app()


if __name__ == "__main__":
    main()
