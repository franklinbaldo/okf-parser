"""Contract tests for the one module that depends on mdformat internals.

If an mdformat upgrade moves or changes any of these, this file fails in one
place with a clear reason, rather than the formatter silently changing output.
"""

from __future__ import annotations

import mdformat
import mdformat.plugins
import pytest

# Imported at module scope on purpose: if a future mdformat moves any of these,
# this module fails to import and the contract breach is impossible to miss.
from mdformat._util import build_mdit
from mdformat.renderer import DEFAULT_RENDERERS
from mdformat.renderer._context import get_list_marker_type, is_tight_list

from okf_parser.markdown_style import (
    EXTENSIONS,
    PLUGIN_NAME,
    format_markdown,
    parse_markdown,
    protected_block_signature,
)


def test_supported_mdformat_version_is_installed() -> None:
    major, minor = (int(part) for part in mdformat.__version__.split(".")[:2])

    assert (major, minor) == (1, 0)


def test_mdformat_still_exposes_the_internals_the_adapter_needs() -> None:
    assert callable(build_mdit)
    assert callable(get_list_marker_type)
    assert callable(is_tight_list)
    assert "ordered_list" in DEFAULT_RENDERERS


def test_conventions_are_registered_and_take_effect() -> None:
    assert PLUGIN_NAME in mdformat.plugins.PARSER_EXTENSIONS
    assert PLUGIN_NAME in EXTENSIONS
    # The default renderer pads this to 01...10; ours must not.
    assert "01." not in format_markdown("".join(f"{i}. i{i}\n" for i in range(1, 11)))


def test_adjacent_ordered_lists_stay_separate() -> None:
    """The contract a naive reimplementation of get_list_marker_type would lose.

    ``1.`` and ``1)`` are two sibling lists; emitting ``.`` for both merges
    them into one, which changes what the document says.
    """
    source = "1. a\n\n1) b\n"

    formatted = format_markdown(source)

    assert _ordered_list_count(formatted) == 2
    assert _ordered_list_count(source) == 2


def test_adjacent_bullet_lists_stay_separate() -> None:
    source = "* a\n\n+ b\n\n- c\n"

    formatted = format_markdown(source)

    assert _bullet_list_count(formatted) == _bullet_list_count(source) == 3


def test_formatting_and_inspection_share_one_dialect() -> None:
    """A divergent dialect is why the previous guard could not see frontmatter."""
    tokens = [token.type for token in parse_markdown("---\ntype: X\n---\n\n# H\n")]

    assert tokens[0] == "front_matter"


def test_gfm_tables_are_visible_to_the_signature() -> None:
    table = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    without_separator = "| a | b |\n| 1 | 2 |\n"

    assert protected_block_signature(table) != protected_block_signature(without_separator)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("101. a\n1. b\n", "11. a\n12. b\n"),
        ("```py\nx=1\n```\n", "```py\nx=2\n```\n"),
        ("```py\nx=1\n```\n", "```js\nx=1\n```\n"),
        ("---\ntype: X\n---\n", "---\ntype: Y\n---\n"),
        ("<div>a</div>\n", "<div>b</div>\n"),
        ("| a |\n|:--|\n| 1 |\n", "| a |\n|--:|\n| 1 |\n"),
    ],
)
def test_signature_detects_protected_changes(left: str, right: str) -> None:
    assert protected_block_signature(left) != protected_block_signature(right)


@pytest.mark.parametrize(
    "source",
    [
        "# H\n\n-   one\n-   two\n",
        "Title\n=====\n\nbody\n",
        "| a | b |\n|:--|--:|\n| 1 | 2 |\n",
        "---\ntype: X\n---\n\n# H\n",
        "    indented = 1\n",
        "[a](b.md)  and  x\n",
        "line1  \nline2\n",
        "~~del~~\n",
        "<div>raw</div>\n",
        "[a][1]\n\n[1]: http://x\n",
    ],
)
def test_signature_does_not_flag_ordinary_formatting(source: str) -> None:
    """Canonicalization that preserves meaning must not read as a change."""
    assert protected_block_signature(format_markdown(source)) == protected_block_signature(source)


def _ordered_list_count(text: str) -> int:
    return sum(1 for token in parse_markdown(text) if token.type == "ordered_list_open")


def _bullet_list_count(text: str) -> int:
    return sum(1 for token in parse_markdown(text) if token.type == "bullet_list_open")
