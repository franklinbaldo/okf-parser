"""Expose okf-parser through Cyclopts and FastMCP."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Annotated, Literal, cast

from cyclopts import App, Parameter
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from okf_parser.duckdb import BundleExportError
from okf_parser.service import (
    apply_bundle,
    check_bundle,
    check_format,
    export_duckdb,
    graph_bundle,
    import_bundle,
    init_bundle,
    inventory_bundle,
    schema_bundle,
    write_format,
)

type McpTransport = Literal["stdio", "http", "sse"]
type SchemaFormat = Literal["json", "zod", "pydantic"]
type ZodImport = Literal["zod", "astro"]
type CliSchemaFormat = Annotated[SchemaFormat, Parameter(name="format")]
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
        text = result.payload
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
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


@app.command
def check(
    path: str,
    *,
    exclude: RepeatableStrings = None,
    require_spec: str | None = None,
    normative_spec: bool = False,
    classify: bool = False,
) -> CliResult[JsonPayload]:
    """Validate every Markdown file recursively as OKF v0.2."""
    payload = check_bundle(
        path,
        exclude or (),
        require_spec,
        normative_spec=normative_spec,
        classify=classify,
    )
    return CliResult(payload, 0 if payload["conformant"] else 1)


@app.command(name="import")
def import_command(  # each argument is an independent public CLI flag.
    source: str,
    path: str,
    *,
    type: str,  # the domain name for this flag is `type`.
    id_column: str | None = None,
    write: bool = False,
    overwrite: bool = False,
) -> CliResult[JsonPayload]:
    """Materialize every row of a DuckDB-readable source (CSV, Parquet, JSON) as a concept."""
    payload = import_bundle(
        source, path, type, id_column=id_column, write=write, overwrite=overwrite
    )
    return CliResult(payload, 1 if payload["duplicate_ids"] else 0)


@app.command
def init(
    path: str,
    *,
    spec_template: str,
    exclude: RepeatableStrings = None,
    write: bool = False,
    infer_schema: bool = False,
) -> CliResult[JsonPayload]:
    """Scaffold a missing specification document, and optionally a starter `.schema.sql`."""
    payload = init_bundle(
        path, spec_template, exclude or (), write=write, infer_schema=infer_schema
    )
    specs = cast("dict[str, object]", payload["specs"])
    schemas = cast("dict[str, object]", payload["schemas"]) if infer_schema else None
    has_collisions = bool(specs["collisions"]) or bool(schemas and schemas["collisions"])
    return CliResult(payload, 1 if has_collisions else 0)


@app.command
def inventory(path: str, *, exclude: RepeatableStrings = None) -> CliResult[JsonPayload]:
    """Count concepts by type using an Ibis relation."""
    return CliResult(inventory_bundle(path, exclude or ()))


@app.command
def graph(path: str, *, exclude: RepeatableStrings = None) -> CliResult[JsonPayload]:
    """Summarize the resolved concept graph with NetworkX."""
    return CliResult(graph_bundle(path, exclude or ()))


@app.command
def schema(  # each argument is an independent public CLI flag.
    path: str,
    *,
    schema_format: CliSchemaFormat = "json",
    infer_types: bool = False,
    cast: RepeatableStrings = None,
    exclude: RepeatableStrings = None,
    zod_import: ZodImport = "zod",
    spec_template: str | None = None,
) -> CliResult[JsonPayload | str]:
    """Export JSON Schema, Zod, or importable Pydantic source."""
    return CliResult(
        schema_bundle(
            path,
            schema_format,
            exclude or (),
            infer_types=infer_types,
            casts=cast or (),
            zod_import=zod_import,
            spec_template=spec_template,
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


@app.command
def apply(  # each argument is an independent public CLI flag.
    path: str,
    *,
    sql: str | None = None,
    type: str | None = None,  # the domain name for this flag is `type`.
    field: str | None = None,
    from_: Annotated[str | None, Parameter(name="from")] = None,
    to: str | None = None,
    write: bool = False,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> CliResult[JsonPayload]:
    """Mutate frontmatter fields via a bounded ALTER TABLE + UPDATE SQL script."""
    payload = apply_bundle(
        path,
        sql=sql,
        type_name=type,
        field_name=field,
        from_value=from_,
        to_value=to,
        write=write,
        exclude=exclude or (),
        spec_template=spec_template,
    )
    return CliResult(payload, 0 if payload["succeeded"] else 1)


def _duckdb_export_payload(
    path: str,
    database: str,
    schema: str,
    *,
    overwrite: bool,
    exclude: RepeatableStrings,
    spec_template: str | None,
) -> JsonPayload:
    """Return the shared DuckDB export payload, including known collision errors."""
    try:
        return export_duckdb(
            path,
            database,
            schema,
            overwrite=overwrite,
            exclude=exclude or (),
            spec_template=spec_template,
        )
    except BundleExportError as exc:
        return {
            "error": str(exc),
            "schema": exc.schema_name,
            "existing_tables": list(exc.tables),
        }


@app.command(name="duckdb")
def duckdb_command(
    path: str,
    database: str = "okf.duckdb",
    schema: str = "okf",
    *,
    overwrite: bool = False,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> CliResult[JsonPayload]:
    """Materialize an OKF bundle into a DuckDB database file."""
    payload = _duckdb_export_payload(
        path,
        database,
        schema,
        overwrite=overwrite,
        exclude=exclude,
        spec_template=spec_template,
    )
    return CliResult(payload, exit_code=1 if "error" in payload else 0)


@app.command
def serve(
    transport: McpTransport = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    allow_write: bool = False,
) -> None:
    """Serve effect-aware MCP tools, exposing explicit commit tools only on opt-in."""
    server = build_mcp(allow_write=allow_write)
    if transport == "stdio":
        server.run()
        return
    server.run(transport=transport, host=host, port=port)


def mcp_check(
    path: str,
    exclude: RepeatableStrings = None,
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
    classify: bool = False,
) -> dict[str, object]:
    """Validate every Markdown file recursively as OKF v0.2."""
    return check_bundle(
        path,
        exclude or (),
        require_spec,
        normative_spec=normative_spec,
        classify=classify,
    )


def mcp_inventory(path: str, exclude: RepeatableStrings = None) -> dict[str, object]:
    """Count concepts by type."""
    return inventory_bundle(path, exclude or ())


def mcp_graph(path: str, exclude: RepeatableStrings = None) -> dict[str, object]:
    """Summarize resolved concept relationships."""
    return graph_bundle(path, exclude or ())


def mcp_schema(
    path: str,
    *,
    schema_format: Annotated[SchemaFormat, Field(alias="format")] = "json",
    infer_types: bool = False,
    cast: RepeatableStrings = None,
    exclude: RepeatableStrings = None,
    zod_import: ZodImport = "zod",
    spec_template: str | None = None,
) -> dict[str, object] | str:
    """Export schemas or validation source from one shared contract."""
    return schema_bundle(
        path,
        schema_format,
        exclude or (),
        infer_types=infer_types,
        casts=cast or (),
        zod_import=zod_import,
        spec_template=spec_template,
    )


def mcp_format_check(path: str, exclude: RepeatableStrings = None) -> dict[str, object]:
    """Check mdformat style without modifying files."""
    return check_format(path, exclude or ())


def mcp_apply_preview(
    path: str,
    *,
    sql: str | None = None,
    type: str | None = None,
    field: str | None = None,
    from_: Annotated[str | None, Field(alias="from")] = None,
    to: str | None = None,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> dict[str, object]:
    """Compute an apply candidate without committing bundle changes."""
    return apply_bundle(
        path,
        sql=sql,
        type_name=type,
        field_name=field,
        from_value=from_,
        to_value=to,
        write=False,
        exclude=exclude or (),
        spec_template=spec_template,
    )


def mcp_apply_write(
    path: str,
    *,
    sql: str | None = None,
    type: str | None = None,
    field: str | None = None,
    from_: Annotated[str | None, Field(alias="from")] = None,
    to: str | None = None,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> dict[str, object]:
    """Commit an apply mutation using the same guarded service path as the CLI."""
    return apply_bundle(
        path,
        sql=sql,
        type_name=type,
        field_name=field,
        from_value=from_,
        to_value=to,
        write=True,
        exclude=exclude or (),
        spec_template=spec_template,
    )


def mcp_init_preview(
    path: str,
    spec_template: str,
    exclude: RepeatableStrings = None,
    *,
    infer_schema: bool = False,
) -> dict[str, object]:
    """Plan missing specification files without creating them."""
    return init_bundle(
        path,
        spec_template,
        exclude or (),
        write=False,
        infer_schema=infer_schema,
    )


def mcp_init_write(
    path: str,
    spec_template: str,
    exclude: RepeatableStrings = None,
    *,
    infer_schema: bool = False,
) -> dict[str, object]:
    """Create missing specification files using the existing scaffold service."""
    return init_bundle(
        path,
        spec_template,
        exclude or (),
        write=True,
        infer_schema=infer_schema,
    )


def mcp_import_preview(
    source: str,
    path: str,
    type: str,
    *,
    id_column: str | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Plan a tabular import without creating or replacing concept files."""
    return import_bundle(
        source,
        path,
        type,
        id_column=id_column,
        write=False,
        overwrite=overwrite,
    )


def mcp_import_write(
    source: str,
    path: str,
    type: str,
    *,
    id_column: str | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Commit a tabular import using the existing import service."""
    return import_bundle(
        source,
        path,
        type,
        id_column=id_column,
        write=True,
        overwrite=overwrite,
    )


def mcp_format_write(path: str, exclude: RepeatableStrings = None) -> dict[str, object]:
    """Rewrite Markdown files into canonical format."""
    return write_format(path, exclude or ())


def mcp_duckdb_export(
    path: str,
    database: str = "okf.duckdb",
    schema: str = "okf",
    *,
    overwrite: bool = False,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> dict[str, object]:
    """Materialize the bundle into a persistent DuckDB database."""
    return _duckdb_export_payload(
        path,
        database,
        schema,
        overwrite=overwrite,
        exclude=exclude,
        spec_template=spec_template,
    )


def _tool_annotations(
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
    open_world: bool,
) -> ToolAnnotations:
    """Build explicit MCP effect metadata without relying on protocol defaults."""
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def build_mcp(*, allow_write: bool = False) -> FastMCP:
    """Construct an isolated MCP profile with optional explicit commit authority."""
    server = FastMCP(
        name="okf-parser",
        instructions=(
            "Deterministic OKF inspection and preview tools. Explicit commit tools are "
            "available only when the server is launched with --allow-write. Tool annotations "
            "describe maximum effects and are hints, not authorization."
        ),
    )

    server.tool(
        name="check",
        annotations=_tool_annotations(
            read_only=True, destructive=False, idempotent=True, open_world=False
        ),
    )(mcp_check)
    server.tool(
        name="inventory",
        annotations=_tool_annotations(
            read_only=True, destructive=False, idempotent=True, open_world=False
        ),
    )(mcp_inventory)
    server.tool(
        name="graph",
        annotations=_tool_annotations(
            read_only=True, destructive=False, idempotent=True, open_world=False
        ),
    )(mcp_graph)
    server.tool(
        name="schema",
        annotations=_tool_annotations(
            read_only=False, destructive=True, idempotent=False, open_world=True
        ),
    )(mcp_schema)
    server.tool(
        name="format_check",
        annotations=_tool_annotations(
            read_only=True, destructive=False, idempotent=True, open_world=False
        ),
    )(mcp_format_check)
    server.tool(
        name="apply_preview",
        annotations=_tool_annotations(
            read_only=False, destructive=True, idempotent=False, open_world=True
        ),
    )(mcp_apply_preview)
    server.tool(
        name="init_preview",
        annotations=_tool_annotations(
            read_only=True, destructive=False, idempotent=True, open_world=False
        ),
    )(mcp_init_preview)
    server.tool(
        name="import_preview",
        annotations=_tool_annotations(
            read_only=True, destructive=False, idempotent=True, open_world=True
        ),
    )(mcp_import_preview)

    if allow_write:
        server.tool(
            name="format_write",
            annotations=_tool_annotations(
                read_only=False, destructive=True, idempotent=True, open_world=False
            ),
        )(mcp_format_write)
        server.tool(
            name="apply_write",
            annotations=_tool_annotations(
                read_only=False, destructive=True, idempotent=False, open_world=True
            ),
        )(mcp_apply_write)
        server.tool(
            name="init_write",
            annotations=_tool_annotations(
                read_only=False, destructive=False, idempotent=True, open_world=False
            ),
        )(mcp_init_write)
        server.tool(
            name="import_write",
            annotations=_tool_annotations(
                read_only=False, destructive=True, idempotent=False, open_world=True
            ),
        )(mcp_import_write)
        server.tool(
            name="duckdb_export",
            annotations=_tool_annotations(
                read_only=False, destructive=True, idempotent=False, open_world=True
            ),
        )(mcp_duckdb_export)

    return server


mcp = build_mcp()


def run_mcp_stdio() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


def main() -> None:
    """Run the Cyclopts command-line application."""
    app()


if __name__ == "__main__":
    main()
