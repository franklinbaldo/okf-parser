"""Expose okf-parser through Cyclopts and FastMCP."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Annotated, Literal

from cyclopts import App, Parameter
from fastmcp import FastMCP
from pydantic import Field

from okf_parser.duckdb import BundleExportError
from okf_parser.service import (
    check_bundle,
    check_format,
    export_duckdb,
    graph_bundle,
    inventory_bundle,
    schema_bundle,
    write_format,
)

type McpTransport = Literal["stdio", "http", "sse"]
type SchemaFormat = Literal["json", "zod"]
type CliSchemaFormat = Annotated[SchemaFormat, Parameter(name="format")]
type McpSchemaFormat = Annotated[SchemaFormat, Field(alias="format")]
type RepeatableStrings = list[str] | None
type JsonPayload = dict[str, object]


@dataclass(frozen=True, slots=True)
class CliResult[PayloadT]:
    """A JSON payload or plain text string and its intended process exit code."""

    payload: PayloadT
    exit_code: int = 0


def _render_cli_result(result: object) -> None:
    """Render stable JSON/text and preserve command-specific exit status."""
    if not isinstance(result, CliResult):
        return
    if isinstance(result.payload, str):
        sys.stdout.write(result.payload + "\n")
    else:
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
def check(path: str, *, exclude: RepeatableStrings = None) -> CliResult[JsonPayload]:
    """Validate every Markdown file recursively as OKF v0.2."""
    payload = check_bundle(path, exclude or ())
    return CliResult(payload, 0 if payload["conformant"] else 1)


@app.command
def inventory(path: str, *, exclude: RepeatableStrings = None) -> CliResult[JsonPayload]:
    """Count concepts by type using an Ibis relation."""
    return CliResult(inventory_bundle(path, exclude or ()))


@app.command
def graph(path: str, *, exclude: RepeatableStrings = None) -> CliResult[JsonPayload]:
    """Summarize the resolved concept graph with NetworkX."""
    return CliResult(graph_bundle(path, exclude or ()))


@app.command
def schema(
    path: str,
    *,
    schema_format: CliSchemaFormat = "json",
    infer_types: bool = False,
    cast: RepeatableStrings = None,
    exclude: RepeatableStrings = None,
) -> CliResult[JsonPayload | str]:
    """Export string-first JSON Schema or Zod, with optional type inference."""
    return CliResult(
        schema_bundle(
            path,
            schema_format,
            exclude or (),
            infer_types=infer_types,
            casts=cast or (),
        )
    )


@app.command(name="format")
def format_command(
    path: str,
    *,
    write: bool = False,
    exclude: RepeatableStrings = None,
) -> CliResult[JsonPayload]:
    """Check mdformat style, writing only when --write is explicit."""
    patterns = exclude or ()
    payload = write_format(path, patterns) if write else check_format(path, patterns)
    return CliResult(payload, 0 if payload["succeeded"] else 1)


@app.command(name="duckdb")
def duckdb_command(
    path: str,
    database: str = "okf.duckdb",
    schema: str = "okf",
    *,
    overwrite: bool = False,
    exclude: RepeatableStrings = None,
) -> CliResult[JsonPayload]:
    """Materialize bundle relations into a DuckDB database."""
    try:
        return CliResult(
            export_duckdb(path, database, schema, overwrite=overwrite, exclude=exclude or ())
        )
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
def mcp_check(path: str, exclude: RepeatableStrings = None) -> dict[str, object]:
    """Validate every Markdown file recursively as OKF v0.2."""
    return check_bundle(path, exclude or ())


@mcp.tool(name="inventory")
def mcp_inventory(path: str, exclude: RepeatableStrings = None) -> dict[str, object]:
    """Count concepts by type."""
    return inventory_bundle(path, exclude or ())


@mcp.tool(name="graph")
def mcp_graph(path: str, exclude: RepeatableStrings = None) -> dict[str, object]:
    """Summarize resolved concept relationships."""
    return graph_bundle(path, exclude or ())


@mcp.tool(name="schema")
def mcp_schema(
    path: str,
    *,
    schema_format: McpSchemaFormat = "json",
    infer_types: bool = False,
    cast: RepeatableStrings = None,
    exclude: RepeatableStrings = None,
) -> dict[str, object] | str:
    """Export string-first schemas, optionally inferring or declaring scalar types."""
    return schema_bundle(
        path,
        schema_format,
        exclude or (),
        infer_types=infer_types,
        casts=cast or (),
    )


@mcp.tool(name="format_check")
def mcp_format_check(path: str, exclude: RepeatableStrings = None) -> dict[str, object]:
    """Check mdformat style without modifying files."""
    return check_format(path, exclude or ())


def run_mcp_stdio() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


def main() -> None:
    """Run the Cyclopts command-line application."""
    app()


if __name__ == "__main__":
    main()
