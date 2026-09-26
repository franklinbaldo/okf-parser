"""Automatic Rust-engine resolution tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from okf_parser import rust_core


def test_missing_binary_is_an_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust_core, "resolve_rust_core", lambda **_: None)

    with pytest.raises(rust_core.NativeBinaryMissingError, match="OKF_CORE"):
        rust_core.native_binary()


def test_incompatible_protocol_is_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "okf-parser"
    fake.write_text("#!/bin/sh\necho '{\"protocol\": 99}'\n", encoding="utf-8")
    fake.chmod(0o755)

    with pytest.raises(rust_core.RustCoreError, match="protocol 1 expected"):
        rust_core.rust_load_bundle(tmp_path, fake)


def test_resolution_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rust_core, "packaged_rust_core", lambda: Path("/package/core"))
    assert rust_core.resolve_rust_core(explicit=Path("/explicit/core")) == Path("/explicit/core")
    assert rust_core.resolve_rust_core(environ={"OKF_CORE": "/env/core"}) == Path("/package/core")

    monkeypatch.setattr(rust_core, "packaged_rust_core", lambda: None)
    assert rust_core.resolve_rust_core(
        environ={"OKF_CORE": "/env/core"}, path_lookup=lambda _: "/path/core"
    ) == Path("/env/core")
    assert rust_core.resolve_rust_core(environ={}, path_lookup=lambda _: "/path/core") == Path(
        "/path/core"
    )
    assert rust_core.resolve_rust_core(environ={}, path_lookup=lambda _: None) is None


def test_packaged_core_discovers_active_interpreter_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / ("okf-parser.exe" if os.name == "nt" else "okf-parser")
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(rust_core, "__file__", str(tmp_path / "missing" / "rust_core.py"))
    monkeypatch.setattr(
        rust_core.sysconfig,
        "get_path",
        lambda name: str(tmp_path) if name == "scripts" else None,
    )

    assert rust_core.packaged_rust_core() == executable


def test_bundle_load_uses_private_engine_command(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = {
        "protocol": 1,
        "root": "/bundle",
        "concepts": [],
        "reserved": [],
        "links": [],
        "diagnostics": [],
        "markdown_count": 0,
        "graph": {
            "nodes": 0,
            "edges": 0,
            "weakly_connected_components": 0,
            "strongly_connected_components": 0,
            "directed_acyclic": True,
        },
    }
    run = Mock(return_value=rust_core.subprocess.CompletedProcess([], 0, json.dumps(empty), ""))
    monkeypatch.setattr(rust_core.subprocess, "run", run)

    rust_core.rust_load_bundle(
        Path("bundle"), Path("/env/bin/okf-parser"), ("cache/**",), read_concurrency=7
    )

    assert run.call_args.args[0] == [
        "/env/bin/okf-parser",
        "__engine-load",
        "bundle",
        "--read-concurrency",
        "7",
        "--exclude",
        "cache/**",
    ]


def test_markdown_facts_use_same_private_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(
        return_value=rust_core.subprocess.CompletedProcess(
            [], 0, '[{"links": [], "headings": []}]', ""
        )
    )
    monkeypatch.setattr(rust_core.subprocess, "run", run)

    rust_core.rust_markdown_facts_batch(("# Note",), Path("/env/bin/okf-parser"))

    assert run.call_args.args[0] == [Path("/env/bin/okf-parser"), "__engine-facts"]
