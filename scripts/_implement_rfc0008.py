from __future__ import annotations

from pathlib import Path


CLI = Path("src/okf_parser/cli.py")
text = CLI.read_text(encoding="utf-8")

text = text.replace(
    "from fastmcp import FastMCP\nfrom pydantic import Field\n",
    "from fastmcp import FastMCP\nfrom mcp.types import ToolAnnotations\nfrom pydantic import Field\n",
)

old_mcp = '''mcp = FastMCP(
    name="okf-parser",
    instructions=(
        "Deterministic tools for validating and inspecting Open Knowledge Format bundles. "
        "Formatting checks are read-only; no tool exposed here rewrites files."
    ),
)


'''
if old_mcp not in text:
    raise SystemExit("global FastMCP block not found")
text = text.replace(old_mcp, "", 1)

old_serve = '''@app.command
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


'''
new_serve = '''@app.command
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


'''
if old_serve not in text:
    raise SystemExit("serve block not found")
text = text.replace(old_serve, new_serve, 1)

start = text.index('@mcp.tool(name="check")')
end = text.index("def run_mcp_stdio()", start)
new_mcp_surface = '''def mcp_check(
    path: str,
    exclude: RepeatableStrings = None,
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
) -> dict[str, object]:
    """Validate every Markdown file recursively as OKF v0.2."""
    return check_bundle(path, exclude or (), require_spec, normative_spec=normative_spec)


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
    """Export canonical schemas, optionally inferring or declaring scalar types."""
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
    return export_duckdb(
        path,
        database,
        schema,
        overwrite=overwrite,
        exclude=exclude or (),
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


'''
text = text[:start] + new_mcp_surface + text[end:]
CLI.write_text(text, encoding="utf-8")


TEST = Path("tests/test_mcp.py")
TEST.write_text(
    '''"""RFC 0008 effect-aware MCP profile and adapter tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastmcp import Client

import okf_parser.cli as cli

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastmcp import FastMCP
    from mcp.types import Tool


async def _list_tools(server: FastMCP) -> list[Tool]:
    async with Client(server) as client:
        return await client.list_tools()


def _tools(server: FastMCP) -> dict[str, Tool]:
    return {tool.name: tool for tool in asyncio.run(_list_tools(server))}


def _annotation_tuple(tool: Tool) -> tuple[bool | None, bool | None, bool | None, bool | None]:
    annotations = tool.annotations
    assert annotations is not None
    return (
        annotations.readOnlyHint,
        annotations.destructiveHint,
        annotations.idempotentHint,
        annotations.openWorldHint,
    )


def test_default_mcp_profile_exposes_previews_but_no_commit_tools() -> None:
    tools = _tools(cli.build_mcp())

    assert set(tools) == {
        "check",
        "inventory",
        "graph",
        "schema",
        "format_check",
        "apply_preview",
        "init_preview",
        "import_preview",
    }


def test_write_mcp_profile_adds_only_explicit_commit_tools() -> None:
    default = set(_tools(cli.build_mcp()))
    writable = set(_tools(cli.build_mcp(allow_write=True)))

    assert writable - default == {
        "format_write",
        "apply_write",
        "init_write",
        "import_write",
        "duckdb_export",
    }
    assert default == set(_tools(cli.build_mcp()))


def test_mcp_effect_annotations_describe_maximum_possible_effect() -> None:
    tools = _tools(cli.build_mcp(allow_write=True))
    expected = {
        "check": (True, False, True, False),
        "inventory": (True, False, True, False),
        "graph": (True, False, True, False),
        "schema": (False, True, False, True),
        "format_check": (True, False, True, False),
        "apply_preview": (False, True, False, True),
        "init_preview": (True, False, True, False),
        "import_preview": (True, False, True, True),
        "format_write": (False, True, True, False),
        "apply_write": (False, True, False, True),
        "init_write": (False, False, True, False),
        "import_write": (False, True, False, True),
        "duckdb_export": (False, True, False, True),
    }

    assert {name: _annotation_tuple(tool) for name, tool in tools.items()} == expected


def test_apply_preview_and_write_share_service_with_only_commit_bit_changed(
    monkeypatch: object,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_apply_bundle(path: str, **kwargs: object) -> dict[str, object]:
        calls.append({"path": path, **kwargs})
        return {"written": bool(kwargs["write"])}

    monkeypatch.setattr(cli, "apply_bundle", fake_apply_bundle)  # type: ignore[attr-defined]

    preview = cli.mcp_apply_preview("bundle", sql="UPDATE x SET y = 1")
    written = cli.mcp_apply_write("bundle", sql="UPDATE x SET y = 1")

    assert preview == {"written": False}
    assert written == {"written": True}
    assert calls[0] | {"write": True} == calls[1]


def test_import_preview_and_write_share_service_with_only_commit_bit_changed(
    monkeypatch: object,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_import_bundle(
        source: str,
        path: str,
        concept_type: str,
        **kwargs: object,
    ) -> dict[str, object]:
        calls.append({"source": source, "path": path, "type": concept_type, **kwargs})
        return {"written": bool(kwargs["write"])}

    monkeypatch.setattr(cli, "import_bundle", fake_import_bundle)  # type: ignore[attr-defined]

    preview = cli.mcp_import_preview("source.csv", "bundle", "Pessoa")
    written = cli.mcp_import_write("source.csv", "bundle", "Pessoa")

    assert preview == {"written": False}
    assert written == {"written": True}
    assert calls[0] | {"write": True} == calls[1]
''',
    encoding="utf-8",
)


DOC = Path("docs/cli.md")
doc = DOC.read_text(encoding="utf-8")
doc = doc.replace(
    "The MCP surface currently exposes `check`, `inventory`, `graph`, `schema` and\n`format_check`. File-creating and mutating operations (`import`, `init`,\n`apply`, `format --write` and `duckdb`) remain CLI-only today.\n",
    "The default MCP profile is commit-disabled and exposes inspection plus faithful\npreview tools. `okf-parser serve --allow-write` adds explicit commit tools; the\nzero-argument `okf-parser-mcp` entry point remains commit-disabled. Tool-level MCP\nannotations describe each tool's maximum possible effect and are descriptive hints,\nnot an authorization boundary.\n",
)
doc = doc.replace("No MCP equivalent today.\n\n## `init`", "MCP tools: `import_preview`; `import_write` with `--allow-write`.\n\n## `init`")
doc = doc.replace("No MCP equivalent today.\n\n## `inventory`", "MCP tools: `init_preview`; `init_write` with `--allow-write`.\n\n## `inventory`")
doc = doc.replace(
    "MCP tool: `format_check`; no write-format MCP tool is exposed today.",
    "MCP tools: `format_check`; `format_write` with `--allow-write`.",
)
doc = doc.replace("No MCP equivalent today.\n\n## `duckdb`", "MCP tools: `apply_preview`; `apply_write` with `--allow-write`. Both accept `spec_template`.\n\n## `duckdb`")
doc = doc.replace(
    "No MCP equivalent today.\n\n## `serve`",
    "MCP tool: `duckdb_export` with `--allow-write`; it also accepts `spec_template`.\n\n## `serve`",
)
doc = doc.replace(
    "uv run okf-parser serve [--transport stdio|http|sse] [--host HOST] [--port PORT]",
    "uv run okf-parser serve [--transport stdio|http|sse] [--host HOST] [--port PORT] [--allow-write]",
)
doc = doc.replace(
    "Runs the MCP server. `stdio` (default) is what `okf-parser-mcp` runs directly;\n`http` and `sse` bind `--host` and `--port` for network transports. It currently\nserves the five MCP tools listed above.",
    "Runs the MCP server. `stdio` (default) is what `okf-parser-mcp` runs directly;\n`http` and `sse` bind `--host` and `--port` for network transports. The default\nprofile exposes `check`, `inventory`, `graph`, `schema`, `format_check`,\n`apply_preview`, `init_preview`, and `import_preview`. `--allow-write` additionally\nexposes `format_write`, `apply_write`, `init_write`, `import_write`, and\n`duckdb_export`. Because `schema` and `apply_preview` may execute trusted RFC 0006\n`.schema.sql`, commit-disabled is intentionally not advertised as globally\nside-effect-free.",
)
DOC.write_text(doc, encoding="utf-8")


README = Path("README.md")
readme = README.read_text(encoding="utf-8")
readme = readme.replace(
    "uv run okf-parser duckdb path/to/bundle knowledge.duckdb --overwrite\n",
    "uv run okf-parser duckdb path/to/bundle knowledge.duckdb --overwrite\nuv run okf-parser serve\nuv run okf-parser serve --allow-write\n",
)
needle = "The command exits with status `1` only when normative errors exist. Broken\ncross-links are warnings because OKF v0.2 explicitly says they do not make a\nbundle non-conformant.\n"
replacement = needle + "\nThe MCP server is commit-disabled by default. `serve --allow-write` exposes explicit\ncommit tools for formatting, relational apply, spec scaffolding, import, and DuckDB\nexport; preview tools remain available without the flag. Effect annotations describe\nmaximum tool effects but are not authorization or sandboxing.\n"
if needle not in readme:
    raise SystemExit("README quick-start anchor not found")
readme = readme.replace(needle, replacement, 1)
README.write_text(readme, encoding="utf-8")


RFC = Path("rfcs/0008-effect-aware-mcp-writes.md")
rfc = RFC.read_text(encoding="utf-8")
rfc = rfc.replace("status: proposed", "status: accepted", 1)
rfc = rfc.replace(
    "| `apply_preview` | `true` | n/a | n/a | `false` |",
    "| `apply_preview` | `false` | `true` | `false` | `true` |",
)
rfc = rfc.replace(
    "| `apply_write` | `false` | `true` | `false` | `false` |",
    "| `apply_write` | `false` | `true` | `false` | `true` |",
)
rfc = rfc.replace(
    "| `duckdb_export` | `false` | `true` | `false` | `false` |",
    "| `duckdb_export` | `false` | `true` | `false` | `true` |",
)
old_decision7 = '''In particular, RFC 0006 intends declared schemas eventually to reach `apply`
and `duckdb`. If an `apply_preview` implementation begins executing the same
trusted declaration SQL, its current `readOnlyHint=true` may cease to be
accurate. The integrating PR must either preserve a genuinely read-only
execution path or change the annotation conservatively.

This is why annotation tests belong next to capability tests.
'''
new_decision7 = '''RFC 0006 now reaches both `apply` and `duckdb`. Therefore `apply_preview`
accepts `spec_template` and can execute trusted declaration SQL even though it does
not commit the candidate frontmatter changes. Its annotation is consequently
non-read-only, destructive, non-idempotent and open-world at the static tool level.
`apply_write` and `duckdb_export` are likewise open-world because they accept the
same declaration capability. This is the concrete example of why annotation tests
belong next to capability tests: a later capability expansion changed the honest
metadata without changing the preview/commit product distinction.
'''
if old_decision7 not in rfc:
    raise SystemExit("RFC decision 7 anchor not found")
rfc = rfc.replace(old_decision7, new_decision7, 1)
rfc = rfc.replace(
    "    exclude: list[str] | None = None,\n) -> dict[str, object]\n\napply_write(...same arguments...) -> dict[str, object]",
    "    exclude: list[str] | None = None,\n    spec_template: str | None = None,\n) -> dict[str, object]\n\napply_write(...same arguments...) -> dict[str, object]",
    1,
)
rfc = rfc.replace(
    "    overwrite: bool = False,\n    exclude: list[str] | None = None,\n) -> dict[str, object]\n```",
    "    overwrite: bool = False,\n    exclude: list[str] | None = None,\n    spec_template: str | None = None,\n) -> dict[str, object]\n```",
    1,
)
rfc = rfc.replace(
    "The first is preferred if FastMCP visibility state is cleanly isolated. The\nsecond is preferable if mutable global visibility leaks between tests or\nmultiple in-process server constructions.\n\nThe RFC does not mandate the mechanism.",
    "The implementation uses the second: `build_mcp(allow_write=...)` constructs an\nisolated FastMCP server and conditionally registers commit tools. This avoids a\nprocess-global visibility switch entirely, so constructing a write-capable profile\ncannot leak authority into the default profile.\n\nThe observable contract remains mechanism-independent.",
)
RFC.write_text(rfc, encoding="utf-8")


# Synchronized release version.
for path in (
    Path("pyproject.toml"),
    Path("typescript/package.json"),
    Path("typescript/src/version.ts"),
    Path("typescript-duckdb/package.json"),
):
    value = path.read_text(encoding="utf-8")
    if "0.18.1" not in value:
        raise SystemExit(f"expected 0.18.1 in {path}")
    path.write_text(value.replace("0.18.1", "0.19.0"), encoding="utf-8")

CHANGELOG = Path("changelog/0.19.0.md")
CHANGELOG.write_text(
    '''---
type: Release
title: okf-parser 0.19.0
description: expose effect-aware MCP previews and opt-in commit tools from the shared service layer
---

# okf-parser 0.19.0

## Effect-aware MCP writes

RFC 0008 is implemented and accepted.

- `okf-parser serve` and the `okf-parser-mcp` entry point remain commit-disabled;
- `serve --allow-write` adds explicit `format_write`, `apply_write`, `init_write`,
  `import_write`, and `duckdb_export` tools;
- dry-run service paths are first-class `apply_preview`, `init_preview`, and
  `import_preview` tools rather than a runtime `write` switch;
- every MCP tool carries explicit `ToolAnnotations` for read-only, destructive,
  idempotent, and open-world effects;
- profiles are constructed independently with `build_mcp()`, so write visibility
  cannot leak between in-process server instances;
- MCP adapters call the same service functions as the CLI and do not duplicate
  mutation, validation, collision, or serialization logic.

Because RFC 0006 declarations are trusted executable DuckDB SQL, `schema`,
`apply_preview`, `apply_write`, and `duckdb_export` are conservatively annotated
for the maximum effect of a legal `spec_template` invocation. The default server is
therefore described as commit-disabled, not globally side-effect-free.
''',
    encoding="utf-8",
)
