"""Tests for the single definition of the version a release is built around."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.project_version import VersionError, main, project_version
from scripts.release_contract import verify_source


def _manifest(path: Path, body: str) -> Path:
    target = path / "Cargo.toml"
    target.write_text(body, encoding="utf-8")
    return target


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603 - local test repository, no shell
        ["git", "-C", str(path), *arguments],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(path: Path) -> str:
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
    return _git(path, "rev-parse", "HEAD")


def test_reads_the_declared_version(tmp_path: Path) -> None:
    target = _manifest(tmp_path, '[workspace.package]\nversion = "1.2.3"\n')
    assert project_version(target) == "1.2.3"


def test_agrees_with_the_release_contract_on_this_repository() -> None:
    """The helper and verify-source must never disagree about the version."""
    root = Path(__file__).resolve().parents[1]
    assert project_version(root / "Cargo.toml") == verify_source(root).version


@pytest.mark.parametrize(
    "body",
    [
        '[tool.other]\nname = "x"\n',
        "[workspace]\nmembers = []\n",
        '[workspace.package]\nedition = "2021"\n',
        '[package]\nversion = "1.2.3"\n',
        '[project]\nversion = "1.2.3"\n',
        'workspace = "invalid"\n',
        '[workspace]\npackage = "invalid"\n',
    ],
)
def test_rejects_a_missing_or_malformed_workspace_version(tmp_path: Path, body: str) -> None:
    with pytest.raises(VersionError):
        project_version(_manifest(tmp_path, body))


@pytest.mark.parametrize(
    "version",
    ["", "1.2", "v1.2.3", "01.2.3", "1.02.3", "1.2.03", "1.2.3a1", "1.2.3-rc.1", "1.2.3+build"],
)
def test_rejects_non_stable_semver(tmp_path: Path, version: str) -> None:
    with pytest.raises(VersionError, match="stable SemVer"):
        project_version(_manifest(tmp_path, f'[workspace.package]\nversion = "{version}"\n'))


@pytest.mark.parametrize("version", ["0.0.0", "0.48.1", "10.20.300"])
def test_accepts_stable_semver(tmp_path: Path, version: str) -> None:
    assert (
        project_version(_manifest(tmp_path, f'[workspace.package]\nversion = "{version}"\n'))
        == version
    )


@pytest.mark.parametrize("value", ["1", "[]", "{}", "true"])
def test_rejects_non_string_versions(tmp_path: Path, value: str) -> None:
    with pytest.raises(VersionError, match="stable SemVer"):
        project_version(_manifest(tmp_path, f"[workspace.package]\nversion = {value}\n"))


def test_rejects_an_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(VersionError, match="cannot read"):
        project_version(tmp_path / "absent.toml")


@pytest.mark.parametrize("contents", [b"[not valid TOML", b"\xff"])
def test_rejects_invalid_toml_or_encoding(tmp_path: Path, contents: bytes) -> None:
    target = tmp_path / "Cargo.toml"
    target.write_bytes(contents)
    with pytest.raises(VersionError, match="cannot read"):
        project_version(target)


def test_cli_prints_one_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = _manifest(tmp_path, '[workspace.package]\nversion = "4.5.6"\n')
    assert main(["--manifest", str(target)]) == 0
    assert capsys.readouterr().out == "4.5.6\n"


def test_cli_defaults_to_workspace_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _manifest(tmp_path, '[workspace.package]\nversion = "4.5.6"\n')
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    assert capsys.readouterr().out == "4.5.6\n"


def test_cli_reports_failure_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--manifest", str(tmp_path / "absent.toml")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "absent.toml" in captured.err


def test_working_tree_never_falls_back_to_pyproject(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _manifest(tmp_path, "[workspace]\nmembers = []\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    assert main(["--manifest", str(target)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "workspace.package.version" in captured.err


def test_reads_workspace_version_from_a_git_ref(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _git(tmp_path, "init", "-q")
    target = _manifest(tmp_path, '[workspace.package]\nversion = "1.2.3"\n')
    revision = _commit(tmp_path)
    target.write_text('[workspace.package]\nversion = "4.5.6"\n', encoding="utf-8")
    assert main(["--manifest", str(target), "--ref", revision]) == 0
    assert capsys.readouterr().out == "1.2.3\n"
    assert project_version(target) == "4.5.6"


@pytest.mark.parametrize("has_cargo", [True, False])
def test_base_ref_before_migration_can_use_legacy_python_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    has_cargo: bool,
) -> None:
    _git(tmp_path, "init", "-q")
    if has_cargo:
        _manifest(tmp_path, "[workspace]\nmembers = []\n")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    revision = _commit(tmp_path)
    target = _manifest(tmp_path, '[workspace.package]\nversion = "4.5.6"\n')
    pyproject.write_text('[project]\ndynamic = ["version"]\n', encoding="utf-8")
    assert main(["--manifest", str(target), "--ref", revision]) == 0
    assert capsys.readouterr().out == "1.2.3\n"


@pytest.mark.parametrize(
    "contents",
    [
        '[workspace.package]\nversion = "invalid"\n',
        '[workspace.package]\nversion = ""\n',
        "[workspace.package]\nversion = []\n",
        'workspace = "invalid"\n',
        "[invalid TOML",
    ],
)
def test_base_ref_does_not_hide_an_invalid_workspace_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    contents: str,
) -> None:
    _git(tmp_path, "init", "-q")
    target = _manifest(tmp_path, contents)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    revision = _commit(tmp_path)
    assert main(["--manifest", str(target), "--ref", revision]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err


def test_base_ref_validates_the_legacy_version(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _git(tmp_path, "init", "-q")
    target = _manifest(tmp_path, "[workspace]\nmembers = []\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "1.2.3-rc.1"\n', encoding="utf-8"
    )
    revision = _commit(tmp_path)
    assert main(["--manifest", str(target), "--ref", revision]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stable SemVer" in captured.err


def test_missing_base_ref_fails_without_using_the_working_tree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _git(tmp_path, "init", "-q")
    target = _manifest(tmp_path, '[workspace.package]\nversion = "1.2.3"\n')
    _commit(tmp_path)
    assert main(["--manifest", str(target), "--ref", "missing-ref"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot resolve Git ref" in captured.err


def test_ref_without_any_version_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _git(tmp_path, "init", "-q")
    target = _manifest(tmp_path, "[workspace]\nmembers = []\n")
    revision = _commit(tmp_path)
    assert main(["--manifest", str(target), "--ref", revision]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "neither workspace.package.version nor a legacy pyproject.toml" in captured.err


def test_ref_outside_git_reports_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _manifest(tmp_path, '[workspace.package]\nversion = "1.2.3"\n')
    assert main(["--manifest", str(target), "--ref", "main"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot find Git repository" in captured.err
