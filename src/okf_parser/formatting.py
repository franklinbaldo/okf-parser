"""Optional Markdown formatting checks, separate from OKF conformance."""

from __future__ import annotations

from typing import TYPE_CHECKING

import mdformat
from pydantic import BaseModel, ConfigDict

from okf_parser.discovery import discover_markdown
from okf_parser.parser import block_structure, ordered_item_markers

if TYPE_CHECKING:
    from pathlib import Path

_EXTENSIONS = frozenset({"frontmatter", "gfm"})


class FormatReport(BaseModel):
    """Result of checking or formatting a Markdown tree."""

    model_config = ConfigDict(frozen=True)

    markdown_count: int
    changed_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...]
    written: bool

    @property
    def clean(self) -> bool:
        """Whether every file was readable and already in canonical mdformat form."""
        return not self.changed_paths and not self.skipped_paths

    @property
    def succeeded(self) -> bool:
        """Whether the run leaves no formatting work behind.

        Rewriting resolves the changed files but never the skipped ones, so
        ``--write`` must not report success while any file went unread.
        """
        if self.skipped_paths:
            return False
        return self.written or not self.changed_paths


def _unpad_ordered_markers(text: str) -> str:
    """Strip the zero padding mdformat adds to keep marker widths even.

    mdformat renders ``1..10`` as ``01.`` through ``10.``, so appending one item
    rewrites every earlier line. Each marker is replaced by matching its literal
    text rather than its position, because a marker may follow a blockquote's
    ``>`` or another list's marker rather than plain indentation.
    """
    lines = text.split("\n")
    # Two ordered items can begin on one line when a list opens on another
    # list's line, so resume each search past the previous replacement.
    searched_to: dict[int, int] = {}
    for line, number, delimiter in ordered_item_markers(text):
        if line >= len(lines) or not number.startswith("0") or number == "0":
            continue
        padded, plain = f"{number}{delimiter}", f"{int(number)}{delimiter}"
        start = lines[line].find(padded, searched_to.get(line, 0))
        if start < 0:
            continue
        lines[line] = lines[line][:start] + plain + lines[line][start + len(padded) :]
        searched_to[line] = start + len(plain)
    return "\n".join(lines)


def _canonical_text(original: str) -> str | None:
    """Return canonical Markdown, or ``None`` if formatting would change meaning.

    Consecutive numbering keeps ``1. 2. 3.`` readable in a diff, but it is only
    safe while every marker stays within CommonMark's nine-digit limit, so the
    plain form is the fallback. Whichever candidate is used must preserve the
    document's block structure; ``--write`` rewrites a whole tree and must never
    silently alter what a file says.
    """
    numbered = mdformat.text(original, extensions=set(_EXTENSIONS), options={"number": True})
    plain = mdformat.text(original, extensions=set(_EXTENSIONS))
    expected = block_structure(original)
    for candidate in (_unpad_ordered_markers(numbered), plain):
        if block_structure(candidate) == expected:
            return candidate
    return None


def format_path(path: Path, *, write: bool = False) -> FormatReport:
    """Check or explicitly rewrite every Markdown file below a path.

    Files that cannot be read - a non-UTF-8 byte sequence, a permission error -
    or that no candidate form can rewrite without changing their block
    structure, are reported as skipped rather than aborting the run, matching
    how ``validate_path`` aggregates instead of failing at the first bad
    document.
    """
    root = path.resolve()
    paths = discover_markdown(root)
    changed: list[str] = []
    skipped: list[str] = []
    pending: list[tuple[Path, str]] = []
    for markdown_path in paths:
        relative = markdown_path.relative_to(root).as_posix()
        try:
            original = markdown_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped.append(relative)
            continue
        formatted = _canonical_text(original)
        if formatted is None:
            skipped.append(relative)
            continue
        if formatted == original:
            continue
        changed.append(relative)
        pending.append((markdown_path, formatted))

    # Every file is formatted before anything is written, so an unexpected
    # failure mid-scan cannot leave the tree half-rewritten.
    if write:
        for markdown_path, formatted in pending:
            markdown_path.write_text(formatted, encoding="utf-8")

    return FormatReport(
        markdown_count=len(paths),
        changed_paths=tuple(changed),
        skipped_paths=tuple(skipped),
        written=write,
    )
