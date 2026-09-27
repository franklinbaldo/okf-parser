"""The canonical Markdown form, checked or written by the binary (``__format``).

The formatter rewrites only syntax (list markers and numbering, ``*``
emphasis, ATX headings, compact tables, hard breaks, blank lines, trailing
whitespace, the final newline and simple frontmatter order) and keeps text,
code and HTML byte for byte. A document the rewrite would change the meaning
of is skipped, and ``write`` replaces every changed file or, on a write
error, none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from okf_parser.rust_core import native_result

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class SkippedDocument(BaseModel):
    """A document formatting left alone, and why."""

    model_config = ConfigDict(frozen=True)

    path: str
    reason: str


class FormatReport(BaseModel):
    """What formatting a Markdown tree found, and did."""

    model_config = ConfigDict(frozen=True)

    markdown_count: int
    clean: bool
    changed_paths: tuple[str, ...]
    skipped: tuple[SkippedDocument, ...]
    succeeded: bool
    written: bool

    @property
    def skipped_paths(self) -> tuple[str, ...]:
        """The paths of the skipped documents."""
        return tuple(document.path for document in self.skipped)


class _FormatRequest(BaseModel):
    """The ``__format`` request the binary validates."""

    model_config = ConfigDict(frozen=True)

    path: str
    exclude: tuple[str, ...]
    write: bool


def format_path(
    path: Path,
    *,
    write: bool = False,
    exclude: Sequence[str] = (),
) -> FormatReport:
    """Check, or with ``write`` rewrite, every Markdown file below ``path``."""
    request = _FormatRequest(path=str(path), exclude=tuple(exclude), write=write)
    return FormatReport.model_validate(native_result("__format", request))
