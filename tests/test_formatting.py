"""Tests for optional Markdown formatting."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.formatting import format_path

if TYPE_CHECKING:
    from pathlib import Path


def test_format_check_is_read_only_and_write_is_explicit(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    original = "---\ntype: Reference\n---\n# Heading\n\n-   item\n"
    path.write_text(original, encoding="utf-8")

    check = format_path(tmp_path)

    assert not check.clean
    assert not check.written
    assert path.read_text(encoding="utf-8") == original

    write = format_path(tmp_path, write=True)

    assert write.written
    assert path.read_text(encoding="utf-8") != original


def test_format_ignores_virtual_environment_markdown(tmp_path: Path) -> None:
    dependency = tmp_path / ".venv" / "README.md"
    dependency.parent.mkdir()
    original = "# Dependency\n\n-   do not rewrite\n"
    dependency.write_text(original, encoding="utf-8")

    report = format_path(tmp_path, write=True)

    assert report.markdown_count == 0
    assert dependency.read_text(encoding="utf-8") == original


def test_format_does_not_follow_markdown_symlinks(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    original = "# Outside\n\n-   do not rewrite\n"
    outside.write_text(original, encoding="utf-8")
    link = tmp_path / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    report = format_path(tmp_path, write=True)

    assert report.markdown_count == 0
    assert outside.read_text(encoding="utf-8") == original
