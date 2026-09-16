"""Automatic Rust-engine resolution tests."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

import okf_parser
import okf_parser.bundle as bundle_module
import okf_parser.duckdb as duckdb_surface
import okf_parser.engine as engine
import okf_parser.service as service
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
    executable = tmp_path / ("okf-parser.exe" if os.name == "nt" else "okf-parser")
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(rust_core, "__file__", str(tmp_path / "missing" / "rust_core.py"))
    monkeypatch.setattr(
        rust_core.sysconfig,
        "get_path",
        lambda name: str(tmp_path) if name == "scripts" else None,
    )

    assert rust_core.packaged_rust_core() == executable


def test_engine_loader_passes_resolved_core_to_native_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = Path("/env/bin/okf-parser")
    expected = object()
    native = Mock(return_value=expected)
    monkeypatch.setattr(engine, "resolve_rust_core", Mock(return_value=resolved))
    monkeypatch.setattr(engine, "_load_bundle_native", native)

    result = engine.load_bundle(Path("bundle"), ("cache/**",))

    assert result is expected
    native.assert_called_once_with(Path("bundle"), ("cache/**",), rust_core=resolved)


def test_engine_validation_uses_same_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_bundle = bundle_module.load_bundle(tmp_path)
    loader = Mock(return_value=native_bundle)
    monkeypatch.setattr(engine, "load_bundle", loader)

    report = engine.validate_path(tmp_path)

    loader.assert_called_once_with(tmp_path, ())
    assert report.root == tmp_path.resolve()
    assert report.is_conformant


def test_high_level_surfaces_share_engine_loader() -> None:
    assert okf_parser.load_bundle is engine.load_bundle
    assert okf_parser.validate_path is engine.validate_path
    assert duckdb_surface.load_bundle is engine.load_bundle
    assert service.load_bundle is engine.load_bundle
    assert service.validate_path is engine.validate_path


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
