"""Regression tests for uv-first release workflow policy."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = (
    Path(".github/workflows/publish.yml"),
    Path(".github/workflows/release-dry-run.yml"),
)
PEP723_HELPERS = (
    "check_no_duckdb_link.py",
    "release_contract.py",
    "native_from_wheel.py",
    "registry_state.py",
)


def _workflow_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_release_workflows_are_uv_first() -> None:
    for path in WORKFLOWS:
        text = _workflow_text(path)
        assert "python -m venv" not in text
        assert "python3 -m venv" not in text
        assert "rustup self uninstall" not in text
        assert re.search(r"(?<!uv )\bpip install\b", text) is None

        for helper in PEP723_HELPERS:
            assert f"uv run --script scripts/{helper}" in text
            assert f"python scripts/{helper}" not in text
            assert f"python3 scripts/{helper}" not in text


def test_public_index_smoke_retries_real_binary_install() -> None:
    publish = _workflow_text(WORKFLOWS[0])
    assert "uv venv --python 3.12" in publish
    assert "uv pip install" in publish
    assert "--refresh-package okf-parser" in publish
    assert "--only-binary :all:" in publish
    assert "https://pypi.org/pypi/" not in publish
    assert "run: sleep 30" not in publish


def test_dry_run_keeps_wheel_and_sdist_paths_distinct() -> None:
    dry_run = _workflow_text(WORKFLOWS[1])
    assert 'if [[ "$artifact" == *.whl ]]; then' in dry_run
    assert 'uv pip install --python "$environment/bin/python" --only-binary :all: "$artifact"' in dry_run
    assert 'uv pip install --python "$environment/bin/python" "$artifact"' in dry_run
