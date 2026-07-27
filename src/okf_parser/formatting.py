"""Optional Markdown formatting checks, separate from OKF conformance."""

from __future__ import annotations

from typing import TYPE_CHECKING

import mdformat
from pydantic import BaseModel, ConfigDict

from okf_parser.discovery import discover_markdown

if TYPE_CHECKING:
    from pathlib import Path


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


def format_path(path: Path, *, write: bool = False) -> FormatReport:
    """Check or explicitly rewrite every Markdown file below a path.

    Files that cannot be read - a non-UTF-8 byte sequence, a permission error -
    are reported as skipped rather than aborting the run, matching how
    ``validate_path`` aggregates instead of failing at the first bad document.
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
        formatted = mdformat.text(
            original,
            extensions={"frontmatter", "gfm"},
            # Keep 1. 2. 3. rather than collapsing every marker to 1. Both
            # render identically, but consecutive numbering keeps the source of
            # a numbered plan readable in a diff.
            options={"number": True},
        )
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
