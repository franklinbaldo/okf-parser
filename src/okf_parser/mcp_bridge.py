"""Run one MCP tool call on behalf of the native ``okf-parser serve``.

The MCP protocol itself (transports, tool schemas, effect annotations) lives
in the Rust binary (``rust-core/src/mcp.rs``, built on ``rmcp``), which also
answers every tool but ``schema`` natively. ``schema`` is still Python and is
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
    RepeatableStrings,
    SchemaFormat,
    ZodImport,
)
from okf_parser.service import (
    schema_bundle,
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


type ToolName = Literal["schema"]

TOOLS: dict[ToolName, Callable[..., object]] = {
    "schema": mcp_schema,
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
