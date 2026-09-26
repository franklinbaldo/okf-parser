"""Automatic Rust-engine resolution tests."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

import okf_parser.bundle as bundle_module
import okf_parser.engine as engine
from okf_parser import rust_core, service


def test_native_mode_skips_all_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rust_core,
        "packaged_rust_core",
        lambda: pytest.fail("native mode must not probe package data"),
    )
    assert (
        rust_core.resolve_rust_core(
            engine="native",
            environ={"OKF_CORE": "/env/core"},
            path_lookup=lambda _: pytest.fail("native mode must not probe PATH"),
        )
        is None
    )


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
    run = Mock(return_value=rust_core.subprocess.CompletedProcess([], 0, "{}", ""))
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


def test_service_read_tools_route_through_automatic_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = tmp_path / "okf-parser"
    calls: list[tuple[Path, Path, tuple[str, ...]]] = []

    monkeypatch.setattr(engine, "resolve_rust_core", lambda **_: native)

    def fake_rust_load_bundle(
        root: Path,
        executable: Path,
        exclude: tuple[str, ...] = (),
        *,
        read_concurrency: int = 32,
    ) -> object:
        del read_concurrency
        calls.append((root, executable, tuple(exclude)))
        return {
            "root": str(root),
            "concepts": [],
            "reserved": [],
            "links": [],
            "diagnostics": [],
            "markdown_count": 0,
        }

    monkeypatch.setattr(bundle_module, "rust_load_bundle", fake_rust_load_bundle)

    service.inventory_bundle(str(tmp_path), ("cache/**",))
    service.graph_bundle(str(tmp_path), ("cache/**",))
    payload = service.check_bundle(str(tmp_path), ("cache/**",))

    assert payload["conformant"] is True
    assert calls == [
        (tmp_path.resolve(), native, ("cache/**",)),
        (tmp_path.resolve(), native, ("cache/**",)),
        (tmp_path.resolve(), native, ("cache/**",)),
    ]
