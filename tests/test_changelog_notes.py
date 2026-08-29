"""Tests for assembling one release's notes from its fragments."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import pytest

from scripts.changelog_notes import FragmentError, fragment_body, fragment_paths, main, render
from scripts.project_version import project_version

if TYPE_CHECKING:
    from pathlib import Path

VERSION = "1.2.3"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _fragment(root: Path, slug: str, title: str, body: str, *, version: str = VERSION) -> Path:
    directory = root / "changelog" / version
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    lines = ["---", "type: Release Note", f"title: {title}", "---", "", body, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_several_changes_share_one_version(tmp_path: Path) -> None:
    """The point of fragments: two PRs contribute without touching one file."""
    _fragment(tmp_path, "b-second", "Second", "- Second change.")
    _fragment(tmp_path, "a-first", "First", "- First change.")
    notes = render(tmp_path, VERSION)
    assert notes.startswith(f"# okf-parser {VERSION}\n\n")
    assert notes.index("- First change.") < notes.index("- Second change.")


def test_order_is_the_sorted_file_name(tmp_path: Path) -> None:
    for slug in ("zulu", "alpha", "mike"):
        _fragment(tmp_path, slug, slug, f"- {slug}")
    assert [path.stem for path in fragment_paths(tmp_path, VERSION)] == ["alpha", "mike", "zulu"]


def test_frontmatter_never_reaches_the_release_body(tmp_path: Path) -> None:
    """v0.45.1 published its `---` block because the raw file was passed through."""
    _fragment(tmp_path, "only", "Only", "- A change.")
    notes = render(tmp_path, VERSION)
    assert "type: Release Note" not in notes
    assert "---" not in notes


def test_a_bom_is_stripped(tmp_path: Path) -> None:
    path = _fragment(tmp_path, "only", "Only", "- A change.")
    path.write_text("﻿" + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert render(tmp_path, VERSION).startswith("# okf-parser")


def test_rejects_a_version_with_no_directory(tmp_path: Path) -> None:
    with pytest.raises(FragmentError, match="does not exist"):
        fragment_paths(tmp_path, VERSION)


def test_rejects_an_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "changelog" / VERSION).mkdir(parents=True)
    with pytest.raises(FragmentError, match=r"no \*\.md fragment"):
        fragment_paths(tmp_path, VERSION)


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("no frontmatter at all\n", "no frontmatter"),
        ("---\ntype: Release Note\n", "unterminated frontmatter"),
        ("---\ntype: Release Note\n---\n\n\n", "no notes"),
    ],
)
def test_rejects_a_malformed_fragment(tmp_path: Path, content: str, match: str) -> None:
    path = _fragment(tmp_path, "broken", "Broken", "- A change.")
    path.write_text(content, encoding="utf-8")
    with pytest.raises(FragmentError, match=match):
        fragment_body(path)


def test_cli_writes_notes_to_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _fragment(tmp_path, "only", "Only", "- A change.")
    assert main([VERSION, "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == f"# okf-parser {VERSION}\n\n- A change.\n"


def test_cli_fails_loudly_on_a_missing_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([VERSION, "--root", str(tmp_path)]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_this_repository_renders_its_own_release_notes() -> None:
    """The release this branch belongs to must be publishable as it stands."""
    version = project_version(REPO_ROOT / "pyproject.toml")
    assert render(REPO_ROOT, version).startswith(f"# okf-parser {version}")
