"""Automatic Rust-engine resolution tests."""

from __future__ import annotations

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
