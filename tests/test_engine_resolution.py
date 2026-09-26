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


def test_every_protocol_call_pins_utf8_regardless_of_locale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(*_args: object, **kwargs: object) -> rust_core.subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return rust_core.subprocess.CompletedProcess([], 1, "", "stop")

    monkeypatch.setattr(rust_core.subprocess, "run", fake_run)
    binary = tmp_path / "okf-parser"
    for call in (
        lambda: rust_core.rust_load_bundle(tmp_path, binary),
        lambda: rust_core.rust_markdown_facts_batch(("# é",), binary),
        lambda: rust_core.call_native(
            "__edit", rust_core.NativeError(kind="io", message="x"), binary
        ),
    ):
        with pytest.raises(rust_core.RustCoreError):
            call()

    assert [(call["encoding"], call["errors"]) for call in calls] == [("utf-8", "strict")] * 3
    assert all("text" not in call for call in calls)
