"""Regression tests for uv-first release workflow policy."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ROOT / ".github/workflows/publish.yml",
    ROOT / ".github/workflows/release-dry-run.yml",
)
PEP723_HELPERS = (
    "check_no_duckdb_link.py",
    "release_contract.py",
    "native_from_wheel.py",
    "registry_state.py",
    "project_version.py",
    "verify_wheel_scripts.py",
    "release_summary.py",
)
# The one `python -c` the workflows may keep: it records which interpreter built
# the release, for the manifest's provenance. Running it through
# `uv run --script` would report the ephemeral script's interpreter instead, so
# converting it would quietly change what the manifest claims.
PROVENANCE_PYTHON_C = "python -c 'import platform; print(platform.python_version())'"


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
            assert f"python scripts/{helper}" not in text
            assert f"python3 scripts/{helper}" not in text
            assert f"python -m scripts.{helper.removesuffix('.py')}" not in text


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
    assert (
        'uv pip install --python "$environment/bin/python" --only-binary :all: "$artifact"'
        in dry_run
    )
    assert 'uv pip install --python "$environment/bin/python" "$artifact"' in dry_run


def test_no_inline_python_beyond_the_provenance_probe() -> None:
    """Standalone Python belongs in a PEP 723 helper, not in a workflow heredoc.

    Inline `python - <<PY` and `python -c` reach for whatever interpreter the
    runner happens to expose, cannot be run or tested off CI, and get copied
    between steps until the copies drift -- the release-set verification carried
    the same wheel-scripts check twice and the project version five times.
    """
    for path in WORKFLOWS:
        text = _workflow_text(path)
        assert "python - <<" not in text
        assert "python3 - <<" not in text
        for line in text.splitlines():
            if "python -c" in line:
                assert PROVENANCE_PYTHON_C in line, line.strip()


def test_release_helpers_are_invoked_as_scripts() -> None:
    invocations = {
        ".github/workflows/publish.yml": (
            "uv run --script scripts/project_version.py",
            "uv run --script scripts/verify_wheel_scripts.py",
            "uv run --script scripts/release_contract.py",
            "uv run --script scripts/registry_state.py",
        ),
        ".github/workflows/release-dry-run.yml": (
            "uv run --script scripts/project_version.py",
            "uv run --script scripts/verify_wheel_scripts.py",
            "uv run --script scripts/release_summary.py",
            "uv run --script scripts/release_contract.py",
            "uv run --script scripts/registry_state.py",
        ),
    }
    for name, expected in invocations.items():
        text = _workflow_text(ROOT / name)
        for invocation in expected:
            assert invocation in text, f"{name}: {invocation}"


def test_every_scripts_helper_declares_pep723() -> None:
    for helper in sorted((ROOT / "scripts").glob("*.py")):
        if helper.name == "__init__.py":
            continue
        header = helper.read_text(encoding="utf-8").splitlines()[:8]
        assert header[0] == "#!/usr/bin/env -S uv run --script", helper.name
        assert "# /// script" in header, helper.name
