"""Automatic Rust-engine resolution tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from okf_parser import rust_core


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
    executable = tmp_path / ("okf-core.exe" if os.name == "nt" else "okf-core")
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(rust_core, "__file__", str(tmp_path / "missing" / "rust_core.py"))
    monkeypatch.setattr(
        rust_core.sysconfig,
        "get_path",
        lambda name: str(tmp_path) if name == "scripts" else None,
    )

    assert rust_core.packaged_rust_core() == executable
