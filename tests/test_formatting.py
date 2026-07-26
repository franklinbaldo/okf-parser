"""Tests for optional Markdown formatting."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_tools.formatting import format_path

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
