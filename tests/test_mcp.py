"""RFC 0008 effect-aware MCP profile and adapter tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastmcp import Client

from okf_parser import cli

if TYPE_CHECKING:
    import pytest
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


def test_mcp_public_schemas_keep_aliases_and_no_dynamic_write_switch() -> None:
    tools = _tools(cli.build_mcp(allow_write=True))

    apply_preview = tools["apply_preview"].inputSchema["properties"]
    apply_write = tools["apply_write"].inputSchema["properties"]
    assert "from" in apply_preview
    assert "from_" not in apply_preview
    assert "spec_template" in apply_preview
    assert "write" not in apply_preview
    assert "from" in apply_write
    assert "write" not in apply_write

    assert "write" not in tools["init_preview"].inputSchema["properties"]
    assert "write" not in tools["import_preview"].inputSchema["properties"]
    assert "spec_template" in tools["duckdb_export"].inputSchema["properties"]


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_apply_bundle(path: str, **kwargs: object) -> dict[str, object]:
        calls.append({"path": path, **kwargs})
        return {"written": bool(kwargs["write"])}

    monkeypatch.setattr(cli, "apply_bundle", fake_apply_bundle)

    preview = cli.mcp_apply_preview("bundle", sql="UPDATE x SET y = 1")
    written = cli.mcp_apply_write("bundle", sql="UPDATE x SET y = 1")

    assert preview == {"written": False}
    assert written == {"written": True}
    assert calls[0] | {"write": True} == calls[1]


def test_import_preview_and_write_share_service_with_only_commit_bit_changed(
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr(cli, "import_bundle", fake_import_bundle)

    preview = cli.mcp_import_preview("source.csv", "bundle", "Pessoa")
    written = cli.mcp_import_write("source.csv", "bundle", "Pessoa")

    assert preview == {"written": False}
    assert written == {"written": True}
    assert calls[0] | {"write": True} == calls[1]


def test_duckdb_export_preserves_cli_collision_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def collide(*_args: object, **_kwargs: object) -> dict[str, object]:
        schema_name = "okf"
        raise cli.BundleExportError(schema_name, ("concepts", "links"))

    monkeypatch.setattr(cli, "export_duckdb", collide)

    payload = cli.mcp_duckdb_export("bundle")

    assert payload["schema"] == "okf"
    assert payload["existing_tables"] == ["concepts", "links"]
    assert "pass overwrite=True" in str(payload["error"])
