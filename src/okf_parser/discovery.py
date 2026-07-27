"""Shared, symlink-safe Markdown discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

IGNORED_DIRECTORIES = frozenset(
    {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".ty_cache", ".venv"}
)
MARKDOWN_SUFFIX = ".md"


def is_markdown_filename(name: str) -> bool:
    """Return whether a filename is Markdown, ignoring extension casing.

    Discovery and link classification must agree, otherwise an ``example.MD``
    file is never validated while links pointing at it are reported unresolved.
    """
    return name.lower().endswith(MARKDOWN_SUFFIX)


def discover_markdown(root: Path) -> tuple[Path, ...]:
    """Return authored Markdown files without traversing environments or symlinks."""
    resolved_root = root.resolve()
    if not resolved_root.is_dir():
        msg = f"Markdown root is not a directory: {resolved_root}"
        raise NotADirectoryError(msg)

    paths: list[Path] = []
    for directory, directory_names, filenames in resolved_root.walk(follow_symlinks=False):
        directory_names[:] = [
            name
            for name in directory_names
            if name not in IGNORED_DIRECTORIES and not (directory / name).is_symlink()
        ]
        paths.extend(
            candidate
            for name in filenames
            if is_markdown_filename(name) and not (candidate := directory / name).is_symlink()
        )
    return tuple(sorted(paths))
