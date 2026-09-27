"""Run one MCP tool call on behalf of the native ``okf-parser serve``.

The MCP protocol itself (transports, tool schemas, effect annotations) lives
in the Rust binary (``rust-core/src/mcp.rs``, built on ``rmcp``), which also
answers ``check``, ``inventory``, ``graph``, ``sql``, ``apply_*``,
``init_*`` and ``duckdb_export`` natively. The tools whose logic is still Python are
answered here: the binary pipes
``{"tool": ..., "arguments": {...}}`` to ``python -m okf_parser.mcp_bridge``
and relays the JSON this module prints.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, validate_call

# `validate_call` resolves these annotations when a tool runs, so they are
# runtime imports.
from okf_parser.cli import (  # noqa: TC001
    ImportConflictPolicy,
    RepeatableStrings,
    SchemaFormat,
    ZodImport,
)
from okf_parser.service import (
    check_format,
    import_bundle,
    schema_bundle,
    write_format,
)

if TYPE_CHECKING:
    from collections.abc import Callable


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


type ToolName = Literal[
    "schema",
    "format_check",
    "import_preview",
    "format_write",
    "import_write",
]

TOOLS: dict[ToolName, Callable[..., object]] = {
    "schema": mcp_schema,
    "format_check": mcp_format_check,
    "import_preview": mcp_import_preview,
    "format_write": mcp_format_write,
    "import_write": mcp_import_write,
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
