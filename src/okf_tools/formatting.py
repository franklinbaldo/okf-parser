"""Optional Markdown formatting checks, separate from OKF conformance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import mdformat

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class FormatReport:
    """Result of checking or formatting a Markdown tree."""

    markdown_count: int
    changed_paths: tuple[str, ...]
    written: bool

    @property
    def clean(self) -> bool:
        """Whether every file was already in canonical mdformat form."""
        return not self.changed_paths


def format_path(path: Path, *, write: bool = False) -> FormatReport:
    """Check or explicitly rewrite every Markdown file below a path."""
    root = path.resolve()
    if not root.is_dir():
        msg = f"Markdown root is not a directory: {root}"
        raise NotADirectoryError(msg)

    paths = sorted(
        candidate
        for candidate in root.rglob("*.md")
        if ".git" not in candidate.relative_to(root).parts
    )
    changed: list[str] = []
    for markdown_path in paths:
        original = markdown_path.read_text(encoding="utf-8")
        formatted = mdformat.text(
            original,
            extensions={"frontmatter", "gfm"},
        )
        if formatted == original:
            continue
        changed.append(markdown_path.relative_to(root).as_posix())
        if write:
            markdown_path.write_text(formatted, encoding="utf-8")
    return FormatReport(len(paths), tuple(changed), write)
