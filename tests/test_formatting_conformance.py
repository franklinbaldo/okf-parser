"""Cross-runtime Markdown formatting contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

import pytest
from markdown_it import MarkdownIt

from okf_parser.formatting import format_path

if TYPE_CHECKING:
    from pathlib import Path as TmpPath


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


def _format(root: TmpPath, text: str) -> str:
    document = root / "case.md"
    document.write_text(text, encoding="utf-8")
    report = format_path(root, write=True)
    assert report.skipped == ()
    return document.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", _CASES, ids=_case_id)
def test_shared_formatting_contract(case: FormattingCase, tmp_path: TmpPath) -> None:
    source = case["source"]
    formatted = _format(tmp_path, source)

    assert _format(tmp_path, formatted) == formatted
    expected_change = case["expect_change"]
    if expected_change is not None:
        assert (formatted != source) is expected_change
    for required in case["required"]:
        assert required in formatted
    for forbidden in case["forbidden"]:
        assert forbidden not in formatted

    tokens = MarkdownIt("commonmark").enable("table").parse(formatted)
    assert sum(token.type == "ordered_list_open" for token in tokens) == case["ordered_lists"]
    assert sum(token.type == "bullet_list_open" for token in tokens) == case["bullet_lists"]
