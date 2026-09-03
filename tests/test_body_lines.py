"""Tests for the shared Markdown body-line contract from RFC 0016."""

from __future__ import annotations

import pytest

from okf_parser.body_lines import body_lines


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", []),
        ("one", ["one"]),
        ("one\n", ["one"]),
        ("one\n\n", ["one", ""]),
        ("one\r\ntwo", ["one", "two"]),
        ("one\rtwo", ["one", "two"]),
        ("one\u0085two", ["one", "two"]),
        ("one\u2028two\u2029three", ["one", "two", "three"]),
        ("\t\u03b1  \u03b2\n  \u03b3\t", ["\t\u03b1  \u03b2", "  \u03b3\t"]),
    ],
)
def test_body_lines_matches_established_splitlines_contract(body: str, expected: list[str]) -> None:
    assert body_lines(body) == expected


def test_body_lines_returns_a_fresh_list() -> None:
    first = body_lines("a\nb")
    second = body_lines("a\nb")

    assert first == second == ["a", "b"]
    assert first is not second
