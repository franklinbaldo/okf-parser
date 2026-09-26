"""Run one MCP tool call on behalf of the native ``okf-parser serve``.

The MCP protocol itself (transports, tool schemas, effect annotations) lives
in the Rust binary (``rust-core/src/mcp.rs``, built on ``rmcp``). Tools whose
logic has not been ported yet are answered here: the binary pipes
``{"tool": ..., "arguments": {...}}`` to ``python -m okf_parser.mcp_bridge``
and relays the JSON this module prints.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, validate_call

from okf_parser.cli import (
    ImportConflictPolicy,
    RepeatableStrings,
    SchemaFormat,
    ZodImport,
    _duckdb_export_payload,
)
from okf_parser.service import (
    apply_bundle,
    check_bundle,
    check_format,
    graph_bundle,
    import_bundle,
    init_bundle,
    inventory_bundle,
    schema_bundle,
    write_format,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def mcp_check(
    path: str,
    exclude: RepeatableStrings = None,
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
    relational_schema: str | None = None,
    classify: bool = False,
) -> dict[str, object]:
    """Validate every Markdown file recursively as OKF v0.2."""
    return check_bundle(
        path,
        exclude or (),
        require_spec,
        normative_spec=normative_spec,
        classify=classify,
        relational_schema=relational_schema,
    )


def mcp_inventory(
    path: str, exclude: RepeatableStrings = None, *, digests: bool = False
) -> dict[str, object]:
    """Count concepts by type and optionally expose deterministic content digests."""
    return inventory_bundle(path, exclude or (), digests=digests)


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
    on_conflict: ImportConflictPolicy = "skip",
) -> dict[str, object]:
    """Plan a tabular import without creating or replacing concept files."""
    return import_bundle(
        source,
        path,
        type,
        id_column=id_column,
        write=False,
        overwrite=overwrite,
        on_conflict=on_conflict,
    )


def mcp_import_write(
    source: str,
    path: str,
    type: str,
    *,
    id_column: str | None = None,
    overwrite: bool = False,
    on_conflict: ImportConflictPolicy = "skip",
    expected_preview_token: str | None = None,
) -> dict[str, object]:
    """Commit a tabular import using the existing import service."""
    return import_bundle(
        source,
        path,
        type,
        id_column=id_column,
        write=True,
        overwrite=overwrite,
        on_conflict=on_conflict,
        expected_preview_token=expected_preview_token,
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


type ToolName = Literal[
    "check",
    "inventory",
    "graph",
    "schema",
    "format_check",
    "apply_preview",
    "init_preview",
    "import_preview",
    "format_write",
    "apply_write",
    "init_write",
    "import_write",
    "duckdb_export",
]

TOOLS: dict[ToolName, Callable[..., object]] = {
    "check": mcp_check,
    "inventory": mcp_inventory,
    "graph": mcp_graph,
    "schema": mcp_schema,
    "format_check": mcp_format_check,
    "apply_preview": mcp_apply_preview,
    "init_preview": mcp_init_preview,
    "import_preview": mcp_import_preview,
    "format_write": mcp_format_write,
    "apply_write": mcp_apply_write,
    "init_write": mcp_init_write,
    "import_write": mcp_import_write,
    "duckdb_export": mcp_duckdb_export,
}
"""Every tool the native server may delegate, keyed by its MCP name."""


class ToolCall(BaseModel):
    """One delegated call, exactly as the native server sends it."""

    model_config = ConfigDict(extra="forbid")

    tool: ToolName
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


def run_tool_call(call: ToolCall) -> object:
    """Validate the arguments against the tool's signature and run it."""
    return validate_call(TOOLS[call.tool])(**call.arguments)


def main() -> None:
    """Read one call from stdin and write its JSON result to stdout."""
    call = ToolCall.model_validate_json(sys.stdin.read())
    json.dump(run_tool_call(call), sys.stdout, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    main()
