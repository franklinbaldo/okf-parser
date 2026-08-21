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

DEFAULT_TOOLS = {
    "check",
    "inventory",
    "graph",
    "schema",
    "format_check",
    "apply_preview",
    "init_preview",
    "import_preview",
}
WRITE_TOOLS = {
    "format_write",
    "apply_write",
    "init_write",
    "import_write",
    "duckdb_export",
}


async def _list_tools(server: FastMCP) -> list[Tool]:
    async with Client(server) as client:
        return await client.list_tools()


def _tools(server: FastMCP) -> dict[str, Tool]:
    return {tool.name: tool for tool in asyncio.run(_list_tools(server))}


def _tool_names(server: FastMCP) -> set[str]:
    return set(_tools(server))


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
    assert _tool_names(cli.build_mcp()) == DEFAULT_TOOLS


def test_mcp_profiles_isolate_authority_when_default_is_built_first() -> None:
    default_server = cli.build_mcp()
    default_before = _tool_names(default_server)

    writable_server = cli.build_mcp(allow_write=True)
    writable = _tool_names(writable_server)
    default_after = _tool_names(default_server)

    assert default_before == DEFAULT_TOOLS
    assert writable == DEFAULT_TOOLS | WRITE_TOOLS
    assert default_after == DEFAULT_TOOLS


def test_mcp_profiles_isolate_authority_when_writable_is_built_first() -> None:
    writable_server = cli.build_mcp(allow_write=True)
    writable_before = _tool_names(writable_server)

    default_server = cli.build_mcp()
    default = _tool_names(default_server)
    writable_after = _tool_names(writable_server)

    assert writable_before == DEFAULT_TOOLS | WRITE_TOOLS
    assert default == DEFAULT_TOOLS
    assert writable_after == DEFAULT_TOOLS | WRITE_TOOLS


def test_mcp_public_schemas_keep_aliases_and_preview_write_pairs_match() -> None:
    tools = _tools(cli.build_mcp(allow_write=True))

    apply_preview = tools["apply_preview"].inputSchema
    apply_write = tools["apply_write"].inputSchema
    assert apply_preview == apply_write
    apply_properties = apply_preview["properties"]
    assert "from" in apply_properties
    assert "from_" not in apply_properties
    assert "spec_template" in apply_properties
    assert "write" not in apply_properties

    init_preview = tools["init_preview"].inputSchema
    init_write = tools["init_write"].inputSchema
    assert init_preview == init_write
    assert "write" not in init_preview["properties"]

    import_preview = tools["import_preview"].inputSchema
    import_write = tools["import_write"].inputSchema
    preview_properties = import_preview["properties"]
    write_properties = import_write["properties"]
    assert "expected_preview_token" not in preview_properties
    assert "expected_preview_token" in write_properties
    assert {
        key: value for key, value in write_properties.items() if key != "expected_preview_token"
    } == preview_properties
    assert import_preview["required"] == import_write["required"]
    assert import_preview["additionalProperties"] == import_write["additionalProperties"]
    assert "write" not in preview_properties
    assert preview_properties["on_conflict"]["enum"] == [
        "skip",
        "verify-identical",
    ]

    assert "classify" in tools["check"].inputSchema["properties"]
    assert "digests" in tools["inventory"].inputSchema["properties"]

    schema_format = tools["schema"].inputSchema["properties"]["format"]
    assert schema_format["enum"] == ["json", "zod", "pydantic", "graphql"]
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


def test_init_preview_and_write_share_service_with_only_commit_bit_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_init_bundle(
        path: str,
        spec_template: str,
        exclude: object,
        **kwargs: object,
    ) -> dict[str, object]:
        calls.append(
            {
                "path": path,
                "spec_template": spec_template,
                "exclude": exclude,
                **kwargs,
            }
        )
        return {"written": bool(kwargs["write"])}

    monkeypatch.setattr(cli, "init_bundle", fake_init_bundle)

    preview = cli.mcp_init_preview("bundle", "types/{type}.md", infer_schema=True)
    written = cli.mcp_init_write("bundle", "types/{type}.md", infer_schema=True)

    assert preview == {"written": False}
    assert written == {"written": True}
    assert calls[0] | {"write": True} == calls[1]


def test_import_preview_and_write_share_service_with_review_binding_on_commit(
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
        result: dict[str, object] = {"written": bool(kwargs["write"])}
        if not kwargs["write"]:
            result["preview_token"] = "opaque-binding"
        return result

    monkeypatch.setattr(cli, "import_bundle", fake_import_bundle)

    preview = cli.mcp_import_preview(
        "source.csv", "bundle", "Pessoa", on_conflict="verify-identical"
    )
    written = cli.mcp_import_write(
        "source.csv",
        "bundle",
        "Pessoa",
        on_conflict="verify-identical",
        expected_preview_token=str(preview["preview_token"]),
    )

    assert preview == {"written": False, "preview_token": "opaque-binding"}
    assert written == {"written": True}
    assert (
        calls[0]
        | {
            "write": True,
            "expected_preview_token": preview["preview_token"],
        }
        == calls[1]
    )


def test_duckdb_export_preserves_cli_collision_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def collide(*_args: object, **_kwargs: object) -> dict[str, object]:
        schema_name = "okf"
        raise cli.BundleExportError(schema_name, ("concepts", "links"))

    monkeypatch.setattr(cli, "export_duckdb", collide)

    payload = cli.mcp_duckdb_export("bundle")

    assert payload["schema"] == "okf"
    assert payload["existing_tables"] == ["concepts", "links"]
    assert "pass overwrite=True" in str(payload["error"])
