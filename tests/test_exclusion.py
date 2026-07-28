"""Tests for path exclusion rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.exclusion import EXCLUSION_FILENAME, ExclusionFileError, ExclusionRules

if TYPE_CHECKING:
    from pathlib import Path


def test_no_patterns_excludes_nothing() -> None:
    rules = ExclusionRules(patterns=())

    assert not rules.excludes("README.md")
    assert not rules.excludes("vendor/a.md")


@pytest.mark.parametrize(
    ("pattern", "relative", "expected"),
    [
        # A directory name takes everything below it, at any depth.
        ("vendor", "vendor/a.md", True),
        ("vendor", "vendor/deep/b.md", True),
        ("vendor", "vendor", True),
        ("vendor", "equipe/a.md", False),
        # A directory name is anchored, so it does not match the same name deeper.
        ("vendor", "libs/vendor/a.md", False),
        ("**/vendor", "libs/vendor/a.md", True),
        ("**/vendor", "vendor/a.md", True),
        # `*` stays inside one segment: this is what lets `*.md` mean
        # "root-level Markdown" without swallowing every bundle below it.
        ("*.md", "README.md", True),
        ("*.md", "items/tarefa.md", False),
        ("**/*.md", "items/tarefa.md", True),
        ("?.md", "a.md", True),
        ("?.md", "ab.md", False),
        ("docs/*.md", "docs/a.md", True),
        ("docs/*.md", "docs/deep/a.md", False),
        ("docs/**", "docs/deep/a.md", True),
    ],
)
def test_pattern_matching(pattern: str, relative: str, *, expected: bool) -> None:
    rules = ExclusionRules(patterns=(pattern,))

    assert rules.excludes(relative) is expected


def test_a_pattern_matching_an_ancestor_excludes_the_descendant() -> None:
    """Pruning a directory has to reach files the pattern never names."""
    rules = ExclusionRules(patterns=("vendor",))

    assert rules.excludes("vendor/nested/deep/concept.md")


def test_glob_metacharacters_in_a_literal_segment_are_not_regex() -> None:
    """A dot is a literal, not "any character"."""
    rules = ExclusionRules(patterns=("a.md",))

    assert rules.excludes("a.md")
    assert not rules.excludes("axmd")


def test_reading_an_absent_file_yields_empty_rules(tmp_path: Path) -> None:
    rules = ExclusionRules.read(tmp_path)

    assert rules.patterns == ()


def test_reading_skips_blank_lines_and_comments(tmp_path: Path) -> None:
    (tmp_path / EXCLUSION_FILENAME).write_text(
        "# vendored dependencies\nvendor\n\n  \n*.md   \n",
        encoding="utf-8",
    )

    rules = ExclusionRules.read(tmp_path)

    assert rules.patterns == ("vendor", "*.md")


def test_an_unreadable_exclusion_file_names_the_path(tmp_path: Path) -> None:
    """Silently ignoring a corrupt ignore file would validate the wrong tree."""
    path = tmp_path / EXCLUSION_FILENAME
    path.write_bytes(b"vendor\n\xff\xfe\n")

    with pytest.raises(ExclusionFileError) as caught:
        ExclusionRules.read(tmp_path)

    assert caught.value.path == path


def test_command_line_patterns_extend_the_file(tmp_path: Path) -> None:
    (tmp_path / EXCLUSION_FILENAME).write_text("vendor\n", encoding="utf-8")

    rules = ExclusionRules.read(tmp_path, extra=("*.md",))

    assert rules.excludes("vendor/a.md")
    assert rules.excludes("README.md")
