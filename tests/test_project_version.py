"""Tests for the single definition of the version a release is built around."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.project_version import VersionError, main, project_version
from scripts.release_contract import verify_source


def _pyproject(path: Path, body: str) -> Path:
    target = path / "pyproject.toml"
    target.write_text(body, encoding="utf-8")
    return target


def test_reads_the_declared_version(tmp_path: Path) -> None:
    target = _pyproject(tmp_path, '[project]\nname = "okf-parser"\nversion = "1.2.3"\n')
    assert project_version(target) == "1.2.3"


def test_agrees_with_the_release_contract_on_this_repository() -> None:
    """The helper and verify-source must never disagree about the version."""
    root = Path(__file__).resolve().parents[1]
    assert project_version(root / "pyproject.toml") == verify_source(root).version


@pytest.mark.parametrize(
    "body",
    [
        '[tool.other]\nname = "x"\n',
        '[project]\nname = "okf-parser"\n',
        '[project]\nname = "okf-parser"\nversion = ""\n',
    ],
)
def test_rejects_a_missing_version(tmp_path: Path, body: str) -> None:
    with pytest.raises(VersionError):
        project_version(_pyproject(tmp_path, body))


def test_rejects_an_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(VersionError):
        project_version(tmp_path / "absent.toml")


def test_cli_prints_one_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = _pyproject(tmp_path, '[project]\nname = "okf-parser"\nversion = "4.5.6"\n')
    assert main(["--pyproject", str(target)]) == 0
    assert capsys.readouterr().out == "4.5.6\n"


def test_cli_reports_failure_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--pyproject", str(tmp_path / "absent.toml")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "absent.toml" in captured.err
