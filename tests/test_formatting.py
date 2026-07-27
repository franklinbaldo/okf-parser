"""Tests for optional Markdown formatting."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from markdown_it import MarkdownIt

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


def _format_once(tmp_path: Path, source: str) -> str:
    path = tmp_path / "plan.md"
    path.write_text(source, encoding="utf-8")
    format_path(tmp_path, write=True)
    return path.read_text(encoding="utf-8")


def _is_ordered_list(text: str) -> bool:
    return any(token.type == "ordered_list_open" for token in MarkdownIt("commonmark").parse(text))


def _no_padded_marker(text: str) -> bool:
    """Whether no zero-padded ordered marker survives, at any indent or container depth."""
    return re.search(r"(?<!\d)0\d+[.)]\s", text) is None


def test_ordered_list_numbering_is_preserved(tmp_path: Path) -> None:
    original = "# Plan\n\n1. first\n2. second\n3. third\n"

    assert _format_once(tmp_path, original) == original


def test_ordered_list_numbering_is_renumbered_when_wrong(tmp_path: Path) -> None:
    result = _format_once(tmp_path, "# Plan\n\n1. first\n1. second\n1. third\n")

    assert result == "# Plan\n\n1. first\n2. second\n3. third\n"


def test_crossing_a_marker_width_boundary_does_not_pad(tmp_path: Path) -> None:
    """Padding 1..10 to 01...10 would rewrite all nine earlier lines."""
    result = _format_once(tmp_path, "".join(f"{index}. item{index}\n" for index in range(1, 11)))

    assert result.splitlines()[0] == "1. item1"
    assert result.splitlines()[-1] == "10. item10"
    assert _no_padded_marker(result)
    assert _is_ordered_list(result)


def test_three_digit_list_does_not_pad(tmp_path: Path) -> None:
    result = _format_once(tmp_path, "".join(f"{index}. i{index}\n" for index in range(1, 101)))

    assert result.splitlines()[0] == "1. i1"
    assert result.splitlines()[-1] == "100. i100"
    assert _no_padded_marker(result)


def test_list_at_the_nine_digit_limit_is_preserved(tmp_path: Path) -> None:
    """Consecutive numbering would overflow to 1000000000 and stop being a list."""
    original = "999999999. first\n1. second\n"

    result = _format_once(tmp_path, original)

    assert _is_ordered_list(result)
    assert "1000000000." not in result


def test_padded_marker_inside_a_code_block_is_left_alone(tmp_path: Path) -> None:
    original = "Text\n\n```\n01. not a list\n02. also not\n```\n"

    result = _format_once(tmp_path, original)

    assert "01. not a list" in result
    assert "02. also not" in result


def test_list_items_with_continuation_content_survive(tmp_path: Path) -> None:
    source = "".join(f"{index}. step{index}\n\n   detail{index}\n\n" for index in range(1, 11))

    result = _format_once(tmp_path, source)

    assert _is_ordered_list(result)
    assert "detail10" in result
    assert _no_padded_marker(result)


def test_list_inside_a_blockquote_is_not_padded(tmp_path: Path) -> None:
    source = "".join(f"> {index}. item{index}\n" for index in range(1, 11))

    result = _format_once(tmp_path, source)

    assert _no_padded_marker(result)
    assert "> 1. item1" in result
    assert "> 10. item10" in result


def test_list_inside_a_bullet_is_not_padded(tmp_path: Path) -> None:
    source = "- intro\n\n" + "".join(f"  {index}. item{index}\n" for index in range(1, 11))

    result = _format_once(tmp_path, source)

    assert _no_padded_marker(result)


def test_list_opening_on_a_bullet_line_is_not_partially_padded(tmp_path: Path) -> None:
    """A line-only match unpads every marker except the one sharing the bullet's line."""
    source = "- " + "".join(
        f"{index}. item{index}\n" if index == 1 else f"  {index}. item{index}\n"
        for index in range(1, 11)
    )

    result = _format_once(tmp_path, source)

    assert _no_padded_marker(result)


def test_two_ordered_markers_on_one_line_are_both_unpadded(tmp_path: Path) -> None:
    source = "1. " + "".join(
        f"{index}. x{index}\n" if index == 1 else f"   {index}. x{index}\n"
        for index in range(1, 11)
    )

    result = _format_once(tmp_path, source)

    assert _no_padded_marker(result)


def test_list_inside_a_bullet_inside_a_blockquote_is_not_padded(tmp_path: Path) -> None:
    source = "> - a\n>\n> " + "".join(
        f"{index}. b{index}\n" if index == 1 else f">   {index}. b{index}\n"
        for index in range(1, 11)
    )

    result = _format_once(tmp_path, source)

    assert _no_padded_marker(result)


@pytest.mark.parametrize(
    "source",
    [
        "".join(f"{index}. i{index}\n" for index in range(1, 11)),
        "".join("1. x\n" for _ in range(12)),
        "999999999. first\n1. second\n",
        "1. a\n   - x\n   - y\n2. b\n",
        "".join(f"{index}. step{index}\n\n   detail{index}\n\n" for index in range(1, 11)),
        "Text\n\n```\n01. not a list\n```\n",
        "".join(f"> {index}. item{index}\n" for index in range(1, 11)),
        "- intro\n\n" + "".join(f"  {index}. item{index}\n" for index in range(1, 11)),
        "- 1. item1\n  2. item2\n",
        "> - a\n>\n> 1. b1\n>   2. b2\n",
    ],
)
def test_formatting_is_idempotent(tmp_path: Path, source: str) -> None:
    """A formatter that flip-flops would make format --check flap in CI."""
    once = _format_once(tmp_path, source)

    assert _format_once(tmp_path, once) == once


def test_a_file_whose_structure_would_change_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "a.md"
    original = "# Heading\n\n1. item\n"
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        "okf_parser.formatting.mdformat.text",
        lambda *_args, **_kwargs: "just a paragraph now\n",
    )

    report = format_path(tmp_path, write=True)

    assert report.skipped_paths == ("a.md",)
    assert report.changed_paths == ()
    assert path.read_text(encoding="utf-8") == original


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
