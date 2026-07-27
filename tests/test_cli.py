"""Tests for command exit codes, which automation depends on."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.cli import format_command

if TYPE_CHECKING:
    from pathlib import Path


def test_format_write_exits_nonzero_when_a_file_was_skipped(tmp_path: Path) -> None:
    (tmp_path / "unreadable.md").write_bytes(b"# Caf\xe9\n")

    result = format_command(str(tmp_path), write=True)

    assert result.exit_code == 1
    assert result.payload["skipped_paths"] == ["unreadable.md"]


def test_format_write_exits_zero_once_every_file_is_formatted(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Heading\n\n-   item\n", encoding="utf-8")

    result = format_command(str(tmp_path), write=True)

    assert result.exit_code == 0
    assert result.payload["changed_paths"] == ["a.md"]


def test_format_check_exits_nonzero_when_a_file_needs_formatting(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Heading\n\n-   item\n", encoding="utf-8")

    assert format_command(str(tmp_path)).exit_code == 1
