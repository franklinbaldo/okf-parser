"""Regression tests for uv-first release workflow policy."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from scripts.project_version import project_version

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ROOT / ".github/workflows/publish.yml",
    ROOT / ".github/workflows/release-dry-run.yml",
)
PEP723_HELPERS = (
    "changelog_notes.py",
    "check_duckdb_shipped.py",
    "fetch_libduckdb.py",
    "ship_libduckdb.py",
    "release_contract.py",
    "native_from_wheel.py",
    "registry_state.py",
    "project_version.py",
    "sync_versions.py",
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


def test_public_index_smoke_installs_duckdb_before_readback() -> None:
    publish = _workflow_text(WORKFLOWS[0])
    smoke = publish.split("  smoke-test-public-index:", 1)[1].split("  finalize:", 1)[0]
    install = 'uv pip install --python "$python" --quiet "duckdb>=1.4,<2"'
    assert install in smoke
    assert smoke.index(install) < smoke.index("import duckdb")


def test_release_registry_requests_have_bounded_network_tolerance() -> None:
    publish = _workflow_text(WORKFLOWS[0])
    environment = publish.split("\nenv:\n", 1)[1].split("\njobs:\n", 1)[0]
    assert 'UV_HTTP_CONNECT_TIMEOUT: "30"' in environment
    assert 'UV_HTTP_TIMEOUT: "120"' in environment
    assert 'UV_HTTP_RETRIES: "3"' in environment


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


def test_the_version_gate_lets_changes_share_a_release() -> None:
    """A version identifies a release, not a pull request.

    The rule this replaces compared the head against `origin/$BASE` and refused
    equality. On a stack the base is the previous PR's branch, so the gate
    itself numbered the #180 -> #186 chain 0.45.3 through 0.45.8, and #175 and
    #178 still asked for numbers that had already shipped. What must never
    happen is reusing a published version, so that is what the gate checks.
    """
    ci = _workflow_text(ROOT / ".github/workflows/ci.yml")
    assert "A versão continua" not in ci
    assert "Cada PR deve adicionar exatamente um changelog" not in ci
    assert "git tag --list 'v*.*.*'" in ci
    assert "não supera a última publicada" in ci


def test_release_notes_are_assembled_from_fragments() -> None:
    publish = _workflow_text(WORKFLOWS[0])
    assert "uv run --script scripts/changelog_notes.py" in publish
    assert 'notes="changelog/${version}.md"' not in publish


def test_the_current_release_has_fragments_not_a_flat_file() -> None:
    version = project_version(ROOT / "Cargo.toml")
    assert not (ROOT / "changelog" / f"{version}.md").exists()
    assert sorted((ROOT / "changelog" / version).glob("*.md"))


def test_version_gate_uses_the_workspace_reader_and_generated_version_check() -> None:
    ci = _workflow_text(ROOT / ".github/workflows/ci.yml")
    gate = ci.split("  version-bump:", 1)[1].split("  quality:", 1)[0]
    assert gate.index("uses: astral-sh/setup-uv@") < gate.index("uv run --script")
    assert "uv run --script scripts/sync_versions.py --check" in gate
    assert "head_version=$(uv run --script scripts/project_version.py)" in gate
    assert 'base_version=$(uv run --script scripts/project_version.py --ref "origin/$BASE")' in gate
    assert "pyproject.toml" not in gate
    assert "read_version()" not in gate
    assert "package_version=" not in gate
    assert '[[ "$base_version" != "$head_version" ]]' in gate
    assert 'git diff --name-only "origin/$BASE...HEAD"' in gate


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - isolated local test repository
        ["git", "-C", str(path), *arguments],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    )


def _commit(path: Path) -> None:
    _git(path, "add", ".")
    _git(
        path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "test",
    )


def _workspace(path: Path, version: str) -> None:
    (path / "Cargo.toml").write_text(
        f'[workspace.package]\nversion = "{version}"\n', encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("base", "head", "published", "fragment", "expected"),
    [
        ("1.2.3", "1.2.3", "1.2.2", "new", "OK: release 1.2.3"),
        ("1.2.3", "1.3.0", "1.2.2", "new", "OK: release 1.3.0"),
        ("1.2.3", "1.2.3", "1.2.3", "new", "não supera a última publicada"),
        ("1.2.3", "1.2.3", "1.3.0", "new", "não supera a última publicada"),
        ("1.3.0", "1.2.3", "1.2.2", "new", "regride em relação"),
        ("1.2.3", "1.2.3", "1.2.2", "inherited", "não contribui nenhum fragmento"),
        ("1.2.3", "1.2.3", "1.2.2", "absent", "precisa de ao menos um fragmento"),
    ],
)
@pytest.mark.parametrize("legacy_base", [True, False])
def test_version_gate_policy_runs_against_git_history(  # noqa: PLR0913
    tmp_path: Path,
    base: str,
    head: str,
    published: str,
    fragment: str,
    expected: str,
    *,
    legacy_base: bool,
) -> None:
    _git(tmp_path, "init", "-q", "--initial-branch=main")
    _git(tmp_path, "remote", "add", "origin", str(tmp_path))
    # Exercise both sides of the migration; HEAD must use only Cargo.toml.
    if not legacy_base:
        _workspace(tmp_path, base)
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{base}"\n', encoding="utf-8")
    note = tmp_path / "changelog" / head / "change.md"
    if fragment == "inherited":
        note.parent.mkdir(parents=True)
        note.write_text("A change already on the base.\n", encoding="utf-8")
    _commit(tmp_path)
    _git(tmp_path, "tag", f"v{published}")
    _git(tmp_path, "checkout", "-qb", "feature")
    _workspace(tmp_path, head)
    (tmp_path / "pyproject.toml").write_text('[project]\ndynamic = ["version"]\n', encoding="utf-8")
    if fragment == "new":
        note.parent.mkdir(parents=True)
        note.write_text("The current PR contributes this change.\n", encoding="utf-8")
    _commit(tmp_path)

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts/project_version.py", scripts / "project_version.py")
    executables = tmp_path / "bin"
    executables.mkdir()
    uv = executables / "uv"
    # Exercise the actual PEP 723 helper without downloading a test interpreter.
    uv.write_text(f'#!/bin/sh\nshift 2\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    uv.chmod(0o755)
    ci = _workflow_text(ROOT / ".github/workflows/ci.yml")
    step = ci.split("      - name: Require an unpublished version and a note fragment", 1)[1]
    script = step.split("        run: |\n", 1)[1].split("\n  quality:", 1)[0]
    result = subprocess.run(  # noqa: S603 - test the checked-in CI script in an isolated repo
        ["bash", "-c", textwrap.dedent(script)],  # noqa: S607
        cwd=tmp_path,
        env={
            **os.environ,
            "BASE": "main",
            "PATH": f"{executables}{os.pathsep}{os.environ['PATH']}",
        },
        capture_output=True,
        check=False,
        text=True,
    )
    assert expected in result.stdout, result.stdout + result.stderr
    assert result.returncode == (0 if expected.startswith("OK:") else 1)


def test_coverage_scope_survives_subprocess_working_directory_changes() -> None:
    """Keep the configured product coverage scope when a child runs in a fixture repo."""
    ci = _workflow_text(ROOT / ".github/workflows/ci.yml")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sources = pyproject["tool"]["coverage"]["run"]["source"]
    # The CLI override must match the configured scope exactly; only its path
    # resolution changes. Passing the config alone leaves source paths relative
    # to each subprocess's cwd and can silently miss actual product execution.
    assert sources == ["src/okf_parser"]
    assert f'--cov="$PWD/{sources[0]}" --cov-config=pyproject.toml' in ci
    assert "--cov-report=term-missing --cov-fail-under=85" in ci
