"""Filesystem-safe parsing primitives for OKF Markdown documents."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt

from okf_parser.models import ParsedDocument

if TYPE_CHECKING:
    from pathlib import Path

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n(.*))?\Z",
    re.DOTALL,
)
RESERVED_FILENAMES = frozenset({"index.md", "log.md"})
_MARKDOWN = MarkdownIt("commonmark")


def is_reserved_document(path: Path) -> bool:
    """Return whether a Markdown file is metadata rather than an OKF concept."""
    return path.name in RESERVED_FILENAMES


class DocumentParseError(ValueError):
    """Raised when one concept document cannot be structurally parsed."""


def parse_document(path: Path) -> ParsedDocument:
    """Parse YAML frontmatter and preserve the Markdown body."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        msg = "document must be valid UTF-8"
        raise DocumentParseError(msg) from exc

    match = _FRONTMATTER_RE.match(text.removeprefix("\ufeff"))
    if match is None:
        msg = "concept must start with YAML frontmatter delimited by ---"
        raise DocumentParseError(msg)

    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML frontmatter: {exc}"
        raise DocumentParseError(msg) from exc

    if value is None:
        frontmatter: dict[str, object] = {}
    elif isinstance(value, dict):
        frontmatter = value
    else:
        msg = "frontmatter must be a YAML mapping"
        raise DocumentParseError(msg)

    return ParsedDocument(path=path, frontmatter=frontmatter, body=match.group(2) or "")


def split_optional_frontmatter(text: str) -> tuple[dict[str, object] | None, str]:
    """Split optional frontmatter for reserved documents."""
    normalized = text.removeprefix("\ufeff")
    if not normalized.startswith("---"):
        return None, normalized
    match = _FRONTMATTER_RE.match(normalized)
    if match is None:
        msg = "invalid YAML frontmatter delimiters"
        raise DocumentParseError(msg)
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        msg = f"invalid YAML frontmatter: {exc}"
        raise DocumentParseError(msg) from exc
    if value is None:
        return {}, match.group(2) or ""
    if not isinstance(value, dict):
        msg = "frontmatter must be a YAML mapping"
        raise DocumentParseError(msg)
    return value, match.group(2) or ""


def concept_id(bundle_root: Path, path: Path) -> str:
    """Derive the normative concept ID from a bundle-relative path."""
    return path.relative_to(bundle_root).with_suffix("").as_posix()


def iter_markdown_links(body: str) -> list[str]:
    """Return non-image Markdown link targets in source order."""
    links: list[str] = []
    pending = list(reversed(_MARKDOWN.parse(body)))
    while pending:
        token = pending.pop()
        if token.children:
            pending.extend(reversed(token.children))
        if token.type != "link_open":
            continue
        destination = token.attrGet("href")
        if isinstance(destination, str):
            links.append(destination)
    return links


def resolve_local_target(bundle_root: Path, source_path: Path, raw_target: str) -> Path | None:
    """Resolve one local Markdown target while preventing bundle escape."""
    split = urlsplit(raw_target)
    if split.scheme or split.netloc or not split.path:
        return None

    decoded = unquote(split.path)
    candidate = (
        bundle_root / decoded.lstrip("/")
        if decoded.startswith("/")
        else source_path.parent / decoded
    )
    resolved = candidate.resolve()
    root = bundle_root.resolve()
    if not resolved.is_relative_to(root):
        return None
    return resolved
