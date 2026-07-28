"""Filesystem-safe parsing primitives for OKF Markdown documents."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import SplitResult, unquote, urlsplit

import yaml
from markdown_it import MarkdownIt
from pydantic import ValidationError

from okf_parser.discovery import is_markdown_filename
from okf_parser.models import FRONTMATTER_ADAPTER, ParsedDocument, YamlValue

if TYPE_CHECKING:
    from pathlib import Path

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(.*?)(?:\r?\n)?---[ \t]*(?:\r?\n(.*))?\Z",
    re.DOTALL,
)
RESERVED_FILENAMES = frozenset({"index.md", "log.md"})
_MARKDOWN = MarkdownIt("commonmark")


def is_reserved_document(path: Path) -> bool:
    """Return whether a Markdown file is metadata rather than an OKF concept."""
    return path.name in RESERVED_FILENAMES


class DocumentParseError(ValueError):
    """Raised when one concept document cannot be structurally parsed."""


def _describe_frontmatter_error(exc: ValidationError) -> str:
    """Summarize a recursive-union ValidationError as one actionable sentence.

    A single fault produces one error per union member, so report the most
    specific one instead of echoing the whole list.
    """
    errors = exc.errors(include_url=False, include_input=False, include_context=False)
    deepest = max(errors, key=lambda item: len(item["loc"]))
    if deepest["loc"][-1:] == ("[key]",):
        return f"frontmatter keys must be strings: {deepest['loc'][-2]}"
    if deepest["type"] == "recursion_loop":
        return "frontmatter contains a cyclic YAML anchor"
    field = deepest["loc"][0] if deepest["loc"] else "frontmatter"
    return f"frontmatter contains an unsupported YAML value: {field}"


def _load_frontmatter(block: str) -> dict[str, YamlValue] | None:
    """Load and validate one frontmatter block, or ``None`` when it is empty."""
    try:
        value = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        msg = f"invalid YAML frontmatter: {exc}"
        raise DocumentParseError(msg) from exc

    if value is None:
        return None
    if not isinstance(value, dict):
        msg = "frontmatter must be a YAML mapping"
        raise DocumentParseError(msg)

    try:
        # Strict: OKF promises to preserve producer-defined frontmatter, so an
        # unsupported YAML value must be reported rather than quietly coerced.
        # Lax validation turns !!binary into str and !!set into a list whose
        # order depends on PYTHONHASHSEED.
        return FRONTMATTER_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = f"invalid YAML frontmatter: {_describe_frontmatter_error(exc)}"
        raise DocumentParseError(msg) from exc


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

    return ParsedDocument(
        path=path,
        frontmatter=_load_frontmatter(match.group(1)) or {},
        body=match.group(2) or "",
    )


def split_optional_frontmatter(text: str) -> tuple[dict[str, YamlValue] | None, str]:
    """Split optional frontmatter for reserved documents."""
    normalized = text.removeprefix("\ufeff")
    if not normalized.startswith("---"):
        return None, normalized
    match = _FRONTMATTER_RE.match(normalized)
    if match is None:
        msg = "invalid YAML frontmatter delimiters"
        raise DocumentParseError(msg)
    return _load_frontmatter(match.group(1)) or {}, match.group(2) or ""


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


def iter_headings(body: str) -> list[tuple[int, str]]:
    """Return ``(level, text)`` for every heading, in source order.

    Uses CommonMark tokens rather than line regexes so that ``#`` characters
    inside fenced code blocks are not mistaken for headings.
    """
    headings: list[tuple[int, str]] = []
    tokens = _MARKDOWN.parse(body)
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        content = inline.content if inline is not None and inline.type == "inline" else ""
        headings.append((int(token.tag.removeprefix("h")), content))
    return headings


def split_link_target(raw_target: str) -> SplitResult | None:
    """Split a raw link target, returning ``None`` when it is not a valid URL.

    ``urlsplit`` raises on inputs such as ``http://[oops/x.md``; an unparseable
    target is simply not a resolvable local link.
    """
    try:
        return urlsplit(raw_target)
    except ValueError:
        return None


def has_markdown_suffix(raw_target: str) -> bool:
    """Return whether a link target's path names a Markdown file."""
    split = split_link_target(raw_target)
    return split is not None and is_markdown_filename(split.path)


def looks_like_frontmatter_link(value: str) -> bool:
    """Return whether a frontmatter string is a link rather than prose.

    Body links are declared by the author; frontmatter links are inferred, so
    require a whitespace-free target to keep sentences ending in ``.md`` out of
    the link table.
    """
    return not any(character.isspace() for character in value) and has_markdown_suffix(value)


def resolve_local_target(bundle_root: Path, source_path: Path, raw_target: str) -> Path | None:
    """Resolve one local Markdown target while preventing bundle escape."""
    split = split_link_target(raw_target)
    if split is None or split.scheme or split.netloc or not split.path:
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
