"""Tests for the Markdown summary rendered from a built release tree."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from scripts.release_summary import main, render

if TYPE_CHECKING:
    from pathlib import Path

_MANIFEST = {
    "version": "1.2.3",
    "repository": "franklinbaldo/okf-parser",
    "commit": "a" * 40,
    "artifacts": [
        {"path": "python/okf_parser-1.2.3.tar.gz", "size": 4096, "sha256": "b" * 64},
        {"path": "npm/okf-parser-1.2.3.tgz", "size": 2048, "sha256": "c" * 64},
    ],
}
_REGISTRY = {
    "entries": [
        {"registry": "pypi", "package": "okf-parser", "state": "absent", "package_exists": True},
        {
            "registry": "npm",
            "package": "okf-parser",
            "state": "present_expected",
            "package_exists": True,
        },
    ],
    "plan": [
        {"registry": "pypi", "package": "okf-parser", "action": "publish"},
        {"registry": "npm", "package": "okf-parser", "action": "skip"},
    ],
}


def _release(root: Path, *, native: tuple[str, ...] = ()) -> Path:
    release = root / "release"
    (release / "native-npm").mkdir(parents=True)
    (release / "manifest.json").write_text(json.dumps(_MANIFEST), encoding="utf-8")
    (release / "registry-state.json").write_text(json.dumps(_REGISTRY), encoding="utf-8")
    for name in native:
        (release / "native-npm" / name).write_bytes(b"")
    return release


def test_renders_artifacts_and_preflight(tmp_path: Path) -> None:
    summary = render(_release(tmp_path))
    assert summary.startswith("## Release dry run 1.2.3\n")
    assert f"Source: `franklinbaldo/okf-parser@{'a' * 40}`" in summary
    assert f"| `python/okf_parser-1.2.3.tar.gz` | 4096 | `{'b' * 64}` |" in summary
    assert "| pypi | `okf-parser` | `absent` | `True` | `publish` |" in summary
    assert "| npm | `okf-parser` | `present_expected` | `True` | `skip` |" in summary
    assert summary.endswith("\n")


def test_lists_native_companions_sorted(tmp_path: Path) -> None:
    release = _release(tmp_path, native=("z-1.2.3.tgz", "a-1.2.3.tgz"))
    rows = [line for line in render(release).splitlines() if "native companion" in line]
    assert rows == [
        "| `native-npm/a-1.2.3.tgz` | native companion | verified separately |",
        "| `native-npm/z-1.2.3.tgz` | native companion | verified separately |",
    ]


def test_cli_writes_the_summary_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release = _release(tmp_path)
    assert main(["--directory", str(release)]) == 0
    assert "## Public registry preflight" in capsys.readouterr().out


@pytest.mark.parametrize("removed", ["manifest.json", "registry-state.json"])
def test_cli_fails_on_an_incomplete_release_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], removed: str
) -> None:
    release = _release(tmp_path)
    (release / removed).unlink()
    assert main(["--directory", str(release)]) == 1
    assert "cannot render the release summary" in capsys.readouterr().err


def test_cli_fails_when_the_plan_omits_an_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A KeyError here used to surface only as a failed CI step."""
    release = _release(tmp_path)
    registry = {**_REGISTRY, "plan": _REGISTRY["plan"][:1]}
    (release / "registry-state.json").write_text(json.dumps(registry), encoding="utf-8")
    assert main(["--directory", str(release)]) == 1
    assert "cannot render the release summary" in capsys.readouterr().err
