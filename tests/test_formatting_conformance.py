"""Cross-runtime Markdown formatting contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

import pytest

from okf_parser.markdown_style import (
    format_markdown,
    parse_markdown,
    protected_block_signature,
)


class FormattingCase(TypedDict):
    """One language-neutral formatter behavior case."""

    name: str
    source: str
    expect_change: bool | None
    ordered_lists: int
    bullet_lists: int
    required: list[str]
    forbidden: list[str]


_CASES = cast(
    "list[FormattingCase]",
    json.loads(
        (Path(__file__).parents[1] / "conformance" / "formatting.json").read_text(encoding="utf-8")
    ),
)


def _case_id(case: FormattingCase) -> str:
    return case["name"]


@pytest.mark.parametrize("case", _CASES, ids=_case_id)
def test_shared_formatting_contract(case: FormattingCase) -> None:
    source = case["source"]
    formatted = format_markdown(source)

    assert protected_block_signature(formatted) == protected_block_signature(source)
    assert format_markdown(formatted) == formatted
    expected_change = case["expect_change"]
    if expected_change is not None:
        assert (formatted != source) is expected_change
    for required in case["required"]:
        assert required in formatted
    for forbidden in case["forbidden"]:
        assert forbidden not in formatted

    tokens = parse_markdown(formatted)
    assert sum(token.type == "ordered_list_open" for token in tokens) == case["ordered_lists"]
    assert sum(token.type == "bullet_list_open" for token in tokens) == case["bullet_lists"]
