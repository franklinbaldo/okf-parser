"""Parse OKF Markdown documents with the native engine.

The binary owns YAML and CommonMark (``okf_engine::parse_text`` and
``markdown_facts``); this module sends it documents in batches and wraps the
answers. Frontmatter is strict: every scalar keeps its authored spelling as a
string, and a YAML tag JSON cannot carry (``!!binary``, ``!!set``) is an
error rather than a coerced value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, JsonValue

from okf_parser.models import MarkdownFacts, ParsedDocument, YamlValue
from okf_parser.rust_core import native_binary, native_result, rust_markdown_facts_batch

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "RESERVED_FILENAMES",
    "DocumentParseError",
    "MarkdownFacts",
    "ParsedText",
    "is_reserved_document",
    "markdown_facts",
    "markdown_facts_batch",
    "parse_document",
    "parse_document_text",
    "parse_texts",
]

RESERVED_FILENAMES = frozenset({"index.md", "log.md"})


def is_reserved_document(path: Path) -> bool:
    """Return whether a Markdown file is metadata rather than an OKF concept."""
    return path.name in RESERVED_FILENAMES


class DocumentParseError(ValueError):
    """Raised when one concept document cannot be structurally parsed."""


class _Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    optional: bool = False


class _ParseRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    documents: tuple[_Document, ...]


class ParsedText(BaseModel):
    """One document the binary parsed: frontmatter, body and digests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    frontmatter: dict[str, YamlValue] | None
    body: str
    source_digest: str
    parsed_digest: str | None


class _Invalid(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    error: str


class _ParseAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    documents: tuple[ParsedText | _Invalid, ...]


def parse_texts(
    texts: Sequence[str], *, optional: bool = False
) -> tuple[ParsedText | DocumentParseError, ...]:
    """Parse many documents in one native call; a failure is returned, not raised.

    With ``optional`` (the reserved ``index.md`` and ``log.md``), a document
    that does not start with ``---`` has no frontmatter instead of failing.
    """
    if not texts:
        return ()
    result: dict[str, JsonValue] = native_result(
        "__parse",
        _ParseRequest(documents=tuple(_Document(text=text, optional=optional) for text in texts)),
    )
    answer = _ParseAnswer.model_validate(result)
    return tuple(
        DocumentParseError(item.error) if isinstance(item, _Invalid) else item
        for item in answer.documents
    )


def parse_document(path: Path) -> ParsedDocument:
    """Parse YAML frontmatter and preserve the Markdown body."""
    try:
        text = path.read_bytes().decode("utf-8")
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
    [parsed] = parse_texts((text,))
    if isinstance(parsed, DocumentParseError):
        raise parsed
    return ParsedDocument(
        path=path,
        frontmatter=parsed.frontmatter or {},
        body=parsed.body,
        source_digest=parsed.source_digest,
        parsed_digest=parsed.parsed_digest or "",
    )


def markdown_facts_batch(bodies: Sequence[str]) -> tuple[MarkdownFacts, ...]:
    """Collect links and headings from many bodies in one native call."""
    if not bodies:
        return ()
    return rust_markdown_facts_batch(bodies, native_binary())


def markdown_facts(body: str) -> MarkdownFacts:
    """Collect links and headings from one CommonMark body."""
    [facts] = markdown_facts_batch((body,))
    return facts
