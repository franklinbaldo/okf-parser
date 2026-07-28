"""The one module that depends on mdformat internals.

``okf-parser`` keeps mdformat as its Markdown canonicalizer and replaces only
the rendering policy for ordered lists. Doing that at the AST level needs a few
symbols mdformat does not export, so every such import is confined here and
pinned by ``tests/test_markdown_style.py``.

Formatting and the structural signature are built from the same parser, so the
two sides cannot drift onto different Markdown dialects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final, cast

import mdformat
import mdformat.plugins
from mdformat.renderer import DEFAULT_RENDERERS, MDRenderer

if TYPE_CHECKING:
    from collections.abc import Mapping

    from markdown_it import MarkdownIt
    from markdown_it.token import Token
    from mdformat.renderer import RenderContext, RenderTreeNode
    from mdformat.renderer.typing import Postprocess, Render


class UnsupportedMdformatError(RuntimeError):
    """Raised when the installed mdformat does not expose what the adapter needs."""

    def __init__(self, missing: str) -> None:
        """Name the symbol whose absence makes the adapter unusable."""
        self.missing = missing
        message = (
            f"the installed mdformat does not provide {missing}; "
            f"okf-parser supports mdformat >=1,<1.1"
        )
        super().__init__(message)


try:  # Fail fast and by name, rather than at the first format call.
    from mdformat._util import build_mdit
except ImportError as exc:  # pragma: no cover - guards a future mdformat
    _MISSING = "mdformat._util.build_mdit"
    raise UnsupportedMdformatError(_MISSING) from exc

try:
    from mdformat.renderer._context import get_list_marker_type, is_tight_list
except ImportError as exc:  # pragma: no cover - guards a future mdformat
    _MISSING = "mdformat.renderer._context.get_list_marker_type/is_tight_list"
    raise UnsupportedMdformatError(_MISSING) from exc

if "ordered_list" not in DEFAULT_RENDERERS:  # pragma: no cover - guards a future mdformat
    _MISSING = 'mdformat.renderer.DEFAULT_RENDERERS["ordered_list"]'
    raise UnsupportedMdformatError(_MISSING)

PLUGIN_NAME: Final = "okf_conventions"
"""Key under which the conventions are registered with mdformat."""

MAX_MARKER: Final = 999_999_999
"""CommonMark allows at most nine digits in an ordered-list marker."""

_ITEM_INDENT: Final = " "
_CONTENT_BLOCKS: Final = frozenset({"fence", "code_block", "html_block", "front_matter"})
# An indented code block and a fenced one carry the same meaning, so converting
# between them must not read as a change.
_EQUIVALENT_KINDS: Final = {"code_block": "code", "fence": "code"}


def _render_ordered_list(node: RenderTreeNode, context: RenderContext) -> str:
    """Render an ordered list with consecutive, never zero-padded markers.

    mdformat pads markers to an even width because it derives each item's
    continuation indent from the marker width, so ``1..10`` renders as ``01.``
    through ``10.`` and appending an item rewrites every line above it. Deciding
    the number here, from the list node itself, avoids padding without needing
    to find and rewrite markers in already-rendered text.
    """
    marker_type = get_list_marker_type(node)
    separator = "\n" if is_tight_list(node) else "\n\n"
    start = node.attrs.get("start")
    first_number = start if isinstance(start, int) else 1
    last_number = first_number + len(node.children) - 1
    # Numbering every item would overflow CommonMark's marker limit and stop
    # the block being a list at all, so keep the plain form instead.
    consecutive = last_number <= MAX_MARKER
    widest = last_number if consecutive else first_number
    indent_width = len(f"{widest}{marker_type}{_ITEM_INDENT}")

    rendered = ""
    with context.indented(indent_width):
        for index, item in enumerate(node.children):
            lines = iter(item.render(context).split("\n"))
            first_line = next(lines)
            # Without consecutive numbering mdformat keeps the start number on
            # the first item and repeats 1 afterwards; match that.
            plain_number = first_number if index == 0 else 1
            number = first_number + index if consecutive else plain_number
            marker = f"{number}{marker_type}"
            item_lines = [f"{marker}{_ITEM_INDENT}{first_line}" if first_line else marker]
            item_lines.extend(" " * indent_width + line if line else "" for line in lines)
            rendered += "\n".join(item_lines)
            if index != len(node.children) - 1:
                rendered += separator
    return rendered


class _OkfConventions:
    """okf-parser's Markdown conventions, layered on mdformat's defaults."""

    CHANGES_AST: ClassVar[bool] = False
    RENDERERS: ClassVar[Mapping[str, Render]] = {"ordered_list": _render_ordered_list}
    POSTPROCESSORS: ClassVar[Mapping[str, Postprocess]] = {}

    @staticmethod
    def update_mdit(_mdit: MarkdownIt) -> None:
        """Leave parsing untouched; these conventions only affect rendering."""


def _register_conventions() -> None:
    """Register the conventions with mdformat without publishing an entry point.

    mdformat types ``PARSER_EXTENSIONS`` as a read-only mapping because it
    normally fills it from entry points. It is a plain dict at runtime, and
    registering in process keeps these conventions internal to this package
    instead of applying to every mdformat user in the environment.
    """
    registry = cast("dict[str, object]", mdformat.plugins.PARSER_EXTENSIONS)
    registry[PLUGIN_NAME] = _OkfConventions


_register_conventions()

EXTENSIONS: Final = ("frontmatter", "gfm", PLUGIN_NAME)
"""The Markdown dialect, used for both formatting and inspection."""


def _parser() -> MarkdownIt:
    return build_mdit(MDRenderer, mdformat_opts={}, extensions=EXTENSIONS, codeformatters=[])


def format_markdown(text: str) -> str:
    """Return the canonical form of one Markdown document."""
    return mdformat.text(text, extensions=EXTENSIONS)


def parse_markdown(text: str) -> list[Token]:
    """Parse with the same dialect used to format, so the two cannot diverge."""
    return _parser().parse(text)


def protected_block_signature(text: str) -> list[tuple[object, ...]]:
    """Return the block-level shape this project guarantees formatting preserves.

    Covered: the sequence, nesting and tag of every block token; block
    attributes, including an ordered list's ``start``; and the content of
    fenced and indented code, raw HTML blocks, and frontmatter.

    Not covered: inline content. Link targets, image sources, emphasis and text
    inside a paragraph or table cell are outside this signature, because
    mdformat legitimately rewrites inline whitespace. This is a structural
    signature over protected blocks, not a proof of semantic equivalence.
    """
    signature: list[tuple[object, ...]] = []
    for token in parse_markdown(text):
        if token.type == "inline":
            continue
        kind = _EQUIVALENT_KINDS.get(token.type, token.type)
        tag = "" if kind == "code" else token.tag
        row: list[object] = [kind, tag, token.level, tuple(sorted((token.attrs or {}).items()))]
        if token.type in _CONTENT_BLOCKS:
            row.append((token.info, token.content))
        signature.append(tuple(row))
    return signature
