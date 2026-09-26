"""RFC 0008 effect-aware MCP profile, served natively by ``okf-parser serve``.

The protocol tests drive the real binary over stdio. The service-sharing tests
exercise ``okf_parser.mcp_bridge``, which the binary delegates unported tools to.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, get_args

import pytest

from okf_parser import cli, mcp_bridge
from okf_parser.rust_core import packaged_rust_core

if TYPE_CHECKING:
    from types import TracebackType

_CONFIGURED = os.environ.get("OKF_CORE")
_BINARY = Path(_CONFIGURED) if _CONFIGURED else packaged_rust_core()
native = pytest.mark.skipif(_BINARY is None, reason="the native okf-parser binary is not built")

type JsonObject = dict[str, Any]
"""A decoded JSON-RPC object; its shape is what the tests assert."""

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


class McpSession:
    """A minimal newline-delimited JSON-RPC client for one ``serve`` process."""

    def __init__(self, *flags: str) -> None:
        """Start the server; the handshake happens on ``__enter__``."""
        assert _BINARY is not None
        self._process = subprocess.Popen(  # noqa: S603 - fixed argv to the binary under test
            [str(_BINARY), "serve", *flags],
            env={**os.environ, "OKF_PYTHON": sys.executable},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._next_id = 0

    def __enter__(self) -> Self:
        """Complete the MCP initialize handshake."""
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "okf-parser-tests", "version": "0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close stdin so the server exits, then reap it."""
        assert self._process.stdin is not None
        self._process.stdin.close()
        self._process.wait(timeout=10)

    def _send(self, message: JsonObject) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def request(self, method: str, params: JsonObject) -> JsonObject:
        """Send one request and return its ``result``."""
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params})
        assert self._process.stdout is not None
        while True:
            response = json.loads(self._process.stdout.readline())
            if response.get("id") == self._next_id:
                assert "error" not in response, response
                return response["result"]

    def tools(self) -> dict[str, JsonObject]:
        """List the served tools by name."""
        return {tool["name"]: tool for tool in self.request("tools/list", {})["tools"]}

    def call(self, name: str, arguments: JsonObject) -> JsonObject:
        """Call one tool and return the raw ``CallToolResult``."""
        return self.request("tools/call", {"name": name, "arguments": arguments})


def _annotation_tuple(tool: JsonObject) -> tuple[object, object, object, object]:
    annotations = tool["annotations"]
    return (
        annotations.get("readOnlyHint"),
        annotations.get("destructiveHint"),
        annotations.get("idempotentHint"),
        annotations.get("openWorldHint"),
    )


def _properties(tool: JsonObject) -> JsonObject:
    return tool["inputSchema"]["properties"]


@native
def test_default_mcp_profile_exposes_previews_but_no_commit_tools() -> None:
    with McpSession() as session:
        assert set(session.tools()) == DEFAULT_TOOLS


@native
def test_allow_write_profile_adds_exactly_the_commit_tools() -> None:
    with McpSession("--allow-write") as session:
        assert set(session.tools()) == DEFAULT_TOOLS | WRITE_TOOLS


@native
def test_mcp_public_schemas_keep_aliases_and_preview_write_pairs_match() -> None:
    with McpSession("--allow-write") as session:
        tools = session.tools()

    apply_properties = _properties(tools["apply_preview"])
    assert tools["apply_preview"]["inputSchema"] == tools["apply_write"]["inputSchema"]
    assert {"from", "type", "spec_template"} <= set(apply_properties)
    assert "from_" not in apply_properties
    assert "write" not in apply_properties

    assert tools["init_preview"]["inputSchema"] == tools["init_write"]["inputSchema"]
    assert "write" not in _properties(tools["init_preview"])

    preview_properties = _properties(tools["import_preview"])
    write_properties = _properties(tools["import_write"])
    assert "expected_preview_token" not in preview_properties
    assert {
        key: value for key, value in write_properties.items() if key != "expected_preview_token"
    } == preview_properties
    assert preview_properties["on_conflict"]["enum"] == ["skip", "verify-identical"]

    assert "classify" in _properties(tools["check"])
    assert "digests" in _properties(tools["inventory"])
    assert _properties(tools["schema"])["format"]["enum"] == ["json", "zod", "pydantic", "graphql"]
    assert "spec_template" in _properties(tools["duckdb_export"])


@native
def test_mcp_effect_annotations_describe_maximum_possible_effect() -> None:
    with McpSession("--allow-write") as session:
        tools = session.tools()
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


def _bundle(root: Path) -> Path:
    (root / "a.md").write_text("---\ntype: Node\n---\n[B](b.md)\n", encoding="utf-8")
    (root / "b.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")
    return root


@native
def test_native_graph_tool_summarizes_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    with McpSession() as session:
        result = session.call("graph", {"path": str(bundle)})

    assert result["isError"] is False
    assert result["structuredContent"] == {
        "root": str(bundle.resolve()),
        "nodes": 2,
        "edges": 1,
        "weakly_connected_components": 1,
        "strongly_connected_components": 2,
        "directed_acyclic": True,
    }


@native
def test_native_inventory_tool_counts_types(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    with McpSession() as session:
        result = session.call("inventory", {"path": str(bundle)})

    assert result["isError"] is False
    assert result["structuredContent"] == {
        "root": str(bundle.resolve()),
        "types": [{"concept_type": "Node", "concept_count": 2}],
    }


@native
def test_delegated_tool_answers_through_the_python_bridge(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    with McpSession() as session:
        result = session.call("format_check", {"path": str(bundle)})

    assert result["isError"] is False
    assert result["structuredContent"] == mcp_bridge.mcp_format_check(str(bundle))


@native
def test_delegated_tool_failure_is_a_tool_error_not_a_crash(tmp_path: Path) -> None:
    with McpSession() as session:
        failed = session.call("check", {"path": str(tmp_path / "missing")})
        recovered = session.call("check", {"path": str(_bundle(tmp_path))})

    assert failed["isError"] is True
    assert "not a directory" in failed["content"][0]["text"]
    assert recovered["isError"] is False


@native
def test_delegated_text_result_stays_text(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    with McpSession() as session:
        result = session.call("schema", {"path": str(bundle), "format": "zod"})

    assert result["isError"] is False
    assert "structuredContent" not in result
    assert "z.object" in result["content"][0]["text"]


@native
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("apply_preview", {"path": ".", "write": True}),
        ("graph", {"path": ".", "bogus": 1}),
    ],
    ids=["delegated", "native"],
)
def test_unknown_argument_is_rejected_at_the_native_boundary(
    tool: str, arguments: JsonObject
) -> None:
    with McpSession("--allow-write") as session:
        result = session.call(tool, arguments)

    assert result["isError"] is True
    assert "unknown field" in result["content"][0]["text"]


@native
def test_defaulted_scalars_keep_concrete_non_nullable_schemas() -> None:
    with McpSession("--allow-write") as session:
        tools = session.tools()

    assert _properties(tools["inventory"])["digests"] == {"type": "boolean", "default": False}
    export = _properties(tools["duckdb_export"])
    assert export["database"] == {"type": "string", "default": "okf.duckdb"}
    assert export["schema"] == {"type": "string", "default": "okf"}
    assert export["overwrite"] == {"type": "boolean", "default": False}
    for tool in tools.values():
        assert tool["inputSchema"]["additionalProperties"] is False, tool["name"]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _initialize_over_http(port: int, host_header: str) -> int:
    """POST an MCP initialize with a chosen ``Host`` header; return the HTTP status."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "okf-parser-tests", "version": "0"},
            },
        }
    )
    deadline = time.monotonic() + 10
    while True:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.putrequest("POST", "/mcp", skip_host=True)
            connection.putheader("Host", host_header)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Accept", "application/json, text/event-stream")
            connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body.encode())
            return connection.getresponse().status
        except ConnectionRefusedError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
        finally:
            connection.close()


@native
@pytest.mark.parametrize(
    ("flags", "expected"),
    [((), 403), (("--allowed-host", "svc.example.com"), 200)],
    ids=["rejected-by-default", "allowed-explicitly"],
)
def test_http_host_validation_is_separate_from_the_bind_address(
    flags: tuple[str, ...], expected: int
) -> None:
    assert _BINARY is not None
    port = _free_port()
    server = subprocess.Popen(  # noqa: S603 - fixed argv to the binary under test
        [str(_BINARY), "serve", "--transport", "http", "--port", str(port), *flags],
        env={**os.environ, "OKF_PYTHON": sys.executable},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _initialize_over_http(port, "svc.example.com") == expected
        assert _initialize_over_http(port, f"127.0.0.1:{port}") == 200
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_bridge_accepts_wire_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_apply_bundle(path: str, **kwargs: object) -> dict[str, object]:
        calls.append({"path": path, **kwargs})
        return {}

    monkeypatch.setattr(mcp_bridge, "apply_bundle", fake_apply_bundle)

    mcp_bridge.run_tool_call(
        mcp_bridge.ToolCall(
            tool="apply_preview",
            arguments={"path": "b", "type": "T", "field": "f", "from": "x", "to": "y"},
        )
    )

    assert calls[0]["type_name"] == "T"
    assert calls[0]["from_value"] == "x"


def test_bridge_rejects_arguments_a_tool_does_not_take() -> None:
    with pytest.raises(ValueError, match="unexpected_keyword_argument"):
        mcp_bridge.run_tool_call(
            mcp_bridge.ToolCall(tool="check", arguments={"path": ".", "write": True})
        )


def test_bridge_serves_every_tool_the_native_server_may_delegate() -> None:
    # `inventory` and `graph` are answered natively and never delegated.
    assert set(mcp_bridge.TOOLS) == (DEFAULT_TOOLS | WRITE_TOOLS) - {"inventory", "graph"}
    assert set(get_args(mcp_bridge.ToolName.__value__)) == set(mcp_bridge.TOOLS)


def test_bridge_rejects_an_unknown_tool_at_the_boundary() -> None:
    with pytest.raises(ValueError, match="literal_error"):
        mcp_bridge.ToolCall.model_validate({"tool": "rm_rf", "arguments": {}})


def test_apply_preview_and_write_share_service_with_only_commit_bit_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_apply_bundle(path: str, **kwargs: object) -> dict[str, object]:
        calls.append({"path": path, **kwargs})
        return {"written": bool(kwargs["write"])}

    monkeypatch.setattr(mcp_bridge, "apply_bundle", fake_apply_bundle)

    preview = mcp_bridge.mcp_apply_preview("bundle", sql="UPDATE x SET y = 1")
    written = mcp_bridge.mcp_apply_write("bundle", sql="UPDATE x SET y = 1")

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

    monkeypatch.setattr(mcp_bridge, "init_bundle", fake_init_bundle)

    preview = mcp_bridge.mcp_init_preview("bundle", "types/{type}.md", infer_schema=True)
    written = mcp_bridge.mcp_init_write("bundle", "types/{type}.md", infer_schema=True)

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
            binding = f"opaque-binding-{len(calls)}"
            result["preview_token"] = binding
        return result

    monkeypatch.setattr(mcp_bridge, "import_bundle", fake_import_bundle)

    preview = mcp_bridge.mcp_import_preview(
        "source.csv", "bundle", "Pessoa", on_conflict="verify-identical"
    )
    preview_token = preview["preview_token"]
    assert isinstance(preview_token, str)
    written = mcp_bridge.mcp_import_write(
        "source.csv",
        "bundle",
        "Pessoa",
        on_conflict="verify-identical",
        expected_preview_token=preview_token,
    )

    assert preview["written"] is False
    assert written == {"written": True}
    assert (
        calls[0]
        | {
            "write": True,
            "expected_preview_token": preview_token,
        }
        == calls[1]
    )


def test_duckdb_export_preserves_cli_collision_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def collide(*_args: object, **_kwargs: object) -> dict[str, object]:
        schema_name = "okf"
        raise cli.BundleExportError(schema_name, ("concepts", "links"))

    monkeypatch.setattr(cli, "export_duckdb", collide)

    payload = mcp_bridge.mcp_duckdb_export("bundle")

    assert payload["schema"] == "okf"
    assert payload["existing_tables"] == ["concepts", "links"]
    assert "pass overwrite=True" in str(payload["error"])
