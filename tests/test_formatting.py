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


def test_non_utf8_file_is_skipped_instead_of_raising(tmp_path: Path) -> None:
    (tmp_path / "latin1.md").write_bytes(b"# Caf\xe9\n")

    report = format_path(tmp_path)

    assert report.skipped_paths == ("latin1.md",)
    assert not report.clean


def test_write_formats_readable_files_and_skips_the_rest(tmp_path: Path) -> None:
    formattable = tmp_path / "a-formattable.md"
    original = "# Heading\n\n-   item\n"
    formattable.write_text(original, encoding="utf-8")
    (tmp_path / "b-unreadable.md").write_bytes(b"# Caf\xe9\n")

    report = format_path(tmp_path, write=True)

    assert report.skipped_paths == ("b-unreadable.md",)
    assert report.changed_paths == ("a-formattable.md",)
    assert formattable.read_text(encoding="utf-8") != original


def test_write_does_not_report_success_when_a_file_was_skipped(tmp_path: Path) -> None:
    (tmp_path / "unreadable.md").write_bytes(b"# Caf\xe9\n")

    assert not format_path(tmp_path, write=True).succeeded


def test_write_reports_success_once_every_file_is_formatted(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Heading\n\n-   item\n", encoding="utf-8")

    assert format_path(tmp_path, write=True).succeeded


def test_check_does_not_report_success_when_a_file_needs_formatting(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Heading\n\n-   item\n", encoding="utf-8")

    assert not format_path(tmp_path).succeeded


def test_write_is_all_or_nothing_when_formatting_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "a.md"
    original = "# Heading\n\n-   item\n"
    first.write_text(original, encoding="utf-8")
    (tmp_path / "b.md").write_text(original, encoding="utf-8")

    def explode(text: str, **_kwargs: object) -> str:
        if "b.md" not in text:
            return text.replace("-   item", "- item")
        raise RuntimeError

    monkeypatch.setattr("okf_parser.formatting.mdformat.text", explode)
    (tmp_path / "b.md").write_text(f"{original}<!-- b.md -->\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        format_path(tmp_path, write=True)

    assert first.read_text(encoding="utf-8") == original
