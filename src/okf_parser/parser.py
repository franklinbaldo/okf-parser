"""Filesystem-safe parsing primitives for OKF Markdown documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import SplitResult, unquote, urlsplit

import yaml
from markdown_it import MarkdownIt
from pydantic import ValidationError

from okf_parser.discovery import is_markdown_filename
from okf_parser.models import FRONTMATTER_ADAPTER, ParsedDocument, YamlValue

if TYPE_CHECKING:
    from pathlib import Path

RESERVED_FILENAMES = frozenset({"index.md", "log.md"})
_MARKDOWN = MarkdownIt("commonmark")
_SCALAR_TAGS = (
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:timestamp",
)
_BaseSafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _StringScalarLoader(_BaseSafeLoader):
    """Preserve scalar spelling while retaining YAML mapping/list structure."""


# Copy before filtering: mutating the inherited registry would change the base loader
# process-wide. Remove only implicit scalar typing; null, merge keys and every
# other structural resolver keep their SafeLoader behavior.
_StringScalarLoader.yaml_implicit_resolvers = {
    key: [resolver for resolver in resolvers if resolver[0] not in _SCALAR_TAGS]
    for key, resolvers in _BaseSafeLoader.yaml_implicit_resolvers.items()
}
for _tag in _SCALAR_TAGS:
    _StringScalarLoader.add_constructor(
        _tag,
        lambda loader, node: loader.construct_scalar(node),
    )


def is_reserved_document(path: Path) -> bool:
    """Return whether a Markdown file is metadata rather than an OKF concept."""
    return path.name in RESERVED_FILENAMES


class DocumentParseError(ValueError):
    """Raised when one concept document cannot be structurally parsed."""


@dataclass(frozen=True, slots=True)
class MarkdownFacts:
    """CommonMark facts collected from one parser pass."""

    links: tuple[str, ...]
    headings: tuple[tuple[int, str], ...]


def _is_frontmatter_delimiter(line: str) -> bool:
    """Return whether one complete source line is an OKF `---` delimiter."""
    content = line.removesuffix("\n").removesuffix("\r")
    return content.startswith("---") and not content.removeprefix("---").strip(" \t")


def _split_frontmatter_source(text: str) -> tuple[str, str] | None:
    """Split frontmatter and body with one bounded linear scan."""
    normalized = text.removeprefix("\ufeff")
    opening_end = normalized.find("\n")
    if opening_end < 0 or not _is_frontmatter_delimiter(normalized[: opening_end + 1]):
        return None

    cursor = opening_end + 1
    while cursor <= len(normalized):
        newline = normalized.find("\n", cursor)
        line_end = len(normalized) if newline < 0 else newline + 1
        if _is_frontmatter_delimiter(normalized[cursor:line_end]):
            block_end = cursor
            if block_end > opening_end + 1 and normalized[block_end - 1] == "\n":
                block_end -= 1
                if block_end > opening_end + 1 and normalized[block_end - 1] == "\r":
                    block_end -= 1
            return normalized[opening_end + 1 : block_end], normalized[line_end:]
        if newline < 0:
            break
        cursor = line_end
    return None


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
    """Load one frontmatter block with every ordinary scalar preserved as text."""
    loader = _StringScalarLoader(block)
    try:
        value = loader.get_single_data()
    except yaml.YAMLError as exc:
        msg = f"invalid YAML frontmatter: {exc}"
        raise DocumentParseError(msg) from exc
    finally:
        loader.dispose()

    if value is None:
        return None
    if not isinstance(value, dict):
        msg = "frontmatter must be a YAML mapping"
        raise DocumentParseError(msg)

    try:
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
    return parse_document_text(path, text)


def parse_document_text(path: Path, text: str) -> ParsedDocument:
    """Parse YAML frontmatter and preserve the Markdown body from already-read text.

    Callers that need the exact bytes they parsed from for another purpose
    (hashing, a freshness check) should read once and call this directly,
    rather than `parse_document`, which reads the file itself.
    """
    split = _split_frontmatter_source(text)
    if split is None:
        msg = "concept must start with YAML frontmatter delimited by ---"
        raise DocumentParseError(msg)
    frontmatter, body = split

    return ParsedDocument(
        path=path,
        frontmatter=_load_frontmatter(frontmatter) or {},
        body=body,
    )


def split_optional_frontmatter(text: str) -> tuple[dict[str, YamlValue] | None, str]:
    """Split optional frontmatter for reserved documents."""
    normalized = text.removeprefix("\ufeff")
    if not normalized.startswith("---"):
        return None, normalized
    split = _split_frontmatter_source(normalized)
    if split is None:
        msg = "invalid YAML frontmatter delimiters"
        raise DocumentParseError(msg)
    frontmatter, body = split
    return _load_frontmatter(frontmatter) or {}, body


def concept_id(bundle_root: Path, path: Path) -> str:
    """Derive the normative concept ID from a bundle-relative path."""
    return path.relative_to(bundle_root).with_suffix("").as_posix()


def markdown_facts(body: str) -> MarkdownFacts:
    """Collect links and headings from one CommonMark tokenization."""
    links: list[str] = []
    headings: list[tuple[int, str]] = []
    tokens = _MARKDOWN.parse(body)

    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            content = inline.content if inline is not None and inline.type == "inline" else ""
            headings.append((int(token.tag.removeprefix("h")), content))

        pending = [token]
        while pending:
            current = pending.pop()
            if current.children:
                pending.extend(reversed(current.children))
            if current.type != "link_open":
                continue
            destination = current.attrGet("href")
            if isinstance(destination, str):
                links.append(destination)

    return MarkdownFacts(links=tuple(links), headings=tuple(headings))


def iter_markdown_links(body: str) -> list[str]:
    """Return non-image Markdown link targets in source order."""
    return list(markdown_facts(body).links)


def iter_headings(body: str) -> list[tuple[int, str]]:
    """Return ``(level, text)`` for every heading, in source order."""
    return list(markdown_facts(body).headings)


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
