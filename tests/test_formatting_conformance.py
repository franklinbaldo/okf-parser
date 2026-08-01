"""Cross-runtime Markdown formatting contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from okf_parser.markdown_style import format_markdown, parse_markdown, protected_block_signature

_CASES = json.loads(
    (Path(__file__).parents[1] / "conformance" / "formatting.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: str(case["name"]))
def test_shared_formatting_contract(case: dict[str, object]) -> None:
    source = str(case["source"])
    formatted = format_markdown(source)

    assert protected_block_signature(formatted) == protected_block_signature(source)
    assert format_markdown(formatted) == formatted
    expected_change = case["expect_change"]
    if expected_change is not None:
        assert (formatted != source) is expected_change
    for required in case["required"]:
        assert str(required) in formatted
    for forbidden in case["forbidden"]:
        assert str(forbidden) not in formatted

    tokens = parse_markdown(formatted)
    assert sum(token.type == "ordered_list_open" for token in tokens) == case["ordered_lists"]
    assert sum(token.type == "bullet_list_open" for token in tokens) == case["bullet_lists"]
