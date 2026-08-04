"""Tests for path exclusion rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from okf_parser.exclusion import EXCLUSION_FILENAME, ExclusionFileError, ExclusionRules


def test_no_patterns_excludes_nothing() -> None:
    rules = ExclusionRules(patterns=())

    assert not rules.excludes("README.md")
    assert not rules.excludes("vendor/a.md")


CASES = json.loads(
    (Path(__file__).parent.parent / "conformance" / "exclusion.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_matching_follows_shared_conformance(case: dict[str, object]) -> None:
    """One matching contract, shared byte-for-byte with the TypeScript runtime."""
    patterns = cast("list[str]", case["patterns"])
    rules = ExclusionRules(patterns=tuple(patterns))

    for item in cast("list[dict[str, object]]", case["paths"]):
        relative = cast("str", item["path"])
        is_dir = bool(item.get("is_dir", False))

        assert rules.excludes(relative, is_dir=is_dir) is item["excluded"], relative


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

    # Lines are kept as authored — trailing spaces are `.gitignore` noise that
    # only compilation drops — so what is asserted is what they decide.
    assert rules.patterns == ("vendor", "*.md   ")
    assert rules.excludes("vendor/a.md")
    assert rules.excludes("items/tarefa.md")


def test_a_negation_is_visible_to_the_walk() -> None:
    """Pruning is only safe while nothing below can be re-included."""
    assert not ExclusionRules(patterns=("vendor",)).has_negation
    assert ExclusionRules(patterns=("vendor", "!vendor/knowledge")).has_negation


def test_an_unreadable_exclusion_file_names_the_path(tmp_path: Path) -> None:
    """Silently ignoring a corrupt ignore file would validate the wrong tree."""
    path = tmp_path / EXCLUSION_FILENAME
    path.write_bytes(b"vendor\n\xff\xfe\n")

    with pytest.raises(ExclusionFileError) as caught:
        ExclusionRules.read(tmp_path)

    assert caught.value.path == path


def test_a_directory_pattern_written_with_a_trailing_slash_still_excludes(
    tmp_path: Path,
) -> None:
    """`.okfignore` looks like `.gitignore`, so `vendor/` must not be a silent no-op."""
    (tmp_path / EXCLUSION_FILENAME).write_text("vendor/\n", encoding="utf-8")

    rules = ExclusionRules.read(tmp_path)

    assert rules.excludes("vendor/dep/guide.md")


def test_a_single_string_is_rejected_rather_than_split_into_letters(tmp_path: Path) -> None:
    """`exclude="vendor"` type-checks as a sequence and would exclude nothing."""
    with pytest.raises(TypeError):
        ExclusionRules.read(tmp_path, extra="vendor")


def test_command_line_patterns_extend_the_file(tmp_path: Path) -> None:
    (tmp_path / EXCLUSION_FILENAME).write_text("vendor\n", encoding="utf-8")

    rules = ExclusionRules.read(tmp_path, extra=("*.md",))

    assert rules.excludes("vendor/a.md")
    assert rules.excludes("README.md")
