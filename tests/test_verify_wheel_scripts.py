"""Tests for the one-unified-executable rule each wheel must satisfy."""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

from scripts.verify_wheel_scripts import check, main, wheel_scripts

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_DATA = "okf_parser-1.2.3.data/scripts"


def _wheel(path: Path, names: tuple[str, ...]) -> Path:
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr("okf_parser/__init__.py", "")
        for name in names:
            archive.writestr(name, b"binary")
    return path


def test_accepts_one_posix_executable(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / "one.whl", (f"{_DATA}/okf-parser",))
    assert wheel_scripts(wheel) == [f"{_DATA}/okf-parser"]
    assert check(wheel) is None


def test_accepts_one_windows_executable(tmp_path: Path) -> None:
    assert check(_wheel(tmp_path / "win.whl", (f"{_DATA}/okf-parser.exe",))) is None


def test_rejects_a_wheel_with_no_executable(tmp_path: Path) -> None:
    problem = check(_wheel(tmp_path / "none.whl", ()))
    assert problem is not None
    assert "found []" in problem


def test_rejects_a_companion_executable(tmp_path: Path) -> None:
    """A second script is the packaging drift RFC 0003 forbids."""
    wheel = _wheel(tmp_path / "two.whl", (f"{_DATA}/okf-parser", f"{_DATA}/okf-parser.exe"))
    problem = check(wheel)
    assert problem is not None
    assert "expected one unified okf-parser script" in problem


def test_reports_an_unreadable_wheel(tmp_path: Path) -> None:
    broken = tmp_path / "broken.whl"
    broken.write_bytes(b"not a zip")
    problem = check(broken)
    assert problem is not None
    assert "cannot read wheel" in problem


def test_cli_checks_every_wheel_given(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = _wheel(tmp_path / "good.whl", (f"{_DATA}/okf-parser",))
    bad = _wheel(tmp_path / "bad.whl", ())
    assert main([str(good)]) == 0
    assert "one unified okf-parser script" in capsys.readouterr().out
    assert main([str(good), str(bad)]) == 1
    assert "bad.whl" in capsys.readouterr().err
