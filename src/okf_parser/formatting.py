"""Optional Markdown formatting checks, separate from OKF conformance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from okf_parser.discovery import discover_markdown
from okf_parser.exclusion import ExclusionRules
from okf_parser.frontmatter_order import canonicalize_simple_frontmatter_block
from okf_parser.markdown_style import format_markdown, protected_block_signature
from okf_parser.parser import _split_frontmatter_source

if TYPE_CHECKING:
    from collections.abc import Sequence
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


def _ordered_simple_frontmatter(original: str) -> str:
    """Put only the deliberately simple frontmatter subset in physical canonical order."""
    if not original.startswith("---\n"):
        return original
    split = _split_frontmatter_source(original)
    if split is None:
        return original
    frontmatter, body = split
    ordered = canonicalize_simple_frontmatter_block(frontmatter)
    if ordered == frontmatter:
        return original
    return f"---\n{ordered}\n---\n{body}"


def _canonical_text(original: str) -> str | None:
    """Return canonical Markdown, or ``None`` when formatting is not safe to apply.

    ``--write`` rewrites a whole tree, so a rewrite that changes the document's
    protected block structure is refused rather than saved.  The only intended
    protected-block content change before mdformat runs is the safe physical
    reordering of simple top-level frontmatter fields.  Marker numbering is
    decided while rendering, so nothing here has to inspect or repair the
    formatted text.
    """
    ordered = _ordered_simple_frontmatter(original)
    formatted = format_markdown(ordered)
    if protected_block_signature(formatted) != protected_block_signature(ordered):
        return None
    return formatted


def format_path(
    path: Path,
    *,
    write: bool = False,
    exclude: Sequence[str] = (),
) -> FormatReport:
    """Check or explicitly rewrite every Markdown file below a path.

    A file is reported as skipped, rather than aborting the run, when it cannot
    be read - a non-UTF-8 byte sequence, a permission error - or when
    canonicalizing it would change its protected block structure. This matches
    how ``validate_path`` aggregates instead of failing at the first bad
    document.

    Excluded paths are never read and never written, which is what keeps
    ``--write`` on a repository root away from vendored dependencies.
    """
    root = path.resolve()
    paths = discover_markdown(root, ExclusionRules.read(root, exclude))
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
