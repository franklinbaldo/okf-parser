"""Capability-driven ordered ingestion for large Markdown corpora."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from okf_parser.discovery import discover_markdown
from okf_parser.exclusion import ExclusionRules
from okf_parser.parser import (
    DocumentParseError,
    MarkdownFacts,
    ParsedText,
    is_reserved_document,
    markdown_facts_batch,
    parse_texts,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from okf_parser.models import YamlValue

_BATCH = 256
"""Documents parsed per native call: bounded memory, few subprocesses."""


class IngestionCapability(StrEnum):
    """Per-document work requested by an ingestion consumer."""

    IDENTITY = "identity"
    BODY = "body"
    FRONTMATTER = "frontmatter"
    MARKDOWN_FACTS = "markdown_facts"
    DIGESTS = "digests"


@dataclass(frozen=True, slots=True)
class DocumentEnvelope:
    """One deterministic ingestion result, projected to requested capabilities."""

    ordinal: int
    path: str
    size: int | None
    classification: str
    body: str | None = None
    frontmatter: dict[str, YamlValue] | None = None
    facts: MarkdownFacts | None = None
    source_digest: str | None = None
    parsed_digest: str | None = None
    error: str | None = None


def ingest_documents(
    root: Path,
    capabilities: Sequence[IngestionCapability] = (IngestionCapability.IDENTITY,),
    exclude: Sequence[str] = (),
) -> Iterator[DocumentEnvelope]:
    """Yield Markdown documents in deterministic path order with projection pushdown.

    An identity-only request performs discovery and stat calls but does not open or
    decode file contents. Other capabilities read each file once and reuse the same
    decoded text and frontmatter boundary result.
    """
    root = root.resolve()
    if not root.is_dir():
        msg = f"bundle root is not a directory: {root}"
        raise NotADirectoryError(msg)

    requested = frozenset(capabilities) | {IngestionCapability.IDENTITY}
    needs_text = requested != {IngestionCapability.IDENTITY}
    paths = discover_markdown(root, ExclusionRules.read(root, exclude))

    for start in range(0, len(paths), _BATCH):
        yield from _ingest_batch(
            root, paths[start : start + _BATCH], start, requested, needs_text=needs_text
        )


def _ingest_batch(
    root: Path,
    paths: Sequence[Path],
    first_ordinal: int,
    requested: frozenset[IngestionCapability],
    *,
    needs_text: bool,
) -> Iterator[DocumentEnvelope]:
    """Stat, and when needed read and parse, one batch in path order."""
    envelopes: list[DocumentEnvelope | None] = []
    texts: list[tuple[int, int, str]] = []
    for offset, path in enumerate(paths):
        relative = path.relative_to(root).as_posix()
        classification = "reserved" if is_reserved_document(path) else "candidate"
        try:
            size = path.stat().st_size
            if needs_text:
                texts.append((offset, size, path.read_bytes().decode("utf-8")))
                envelopes.append(None)
            else:
                envelopes.append(
                    DocumentEnvelope(first_ordinal + offset, relative, size, classification)
                )
        except (OSError, UnicodeDecodeError) as exc:
            envelopes.append(_invalid(first_ordinal + offset, relative, exc))

    parsed = parse_texts([text for _, _, text in texts], optional=True)
    bodies = [item.body for item in parsed if not isinstance(item, DocumentParseError)]
    facts = iter(
        markdown_facts_batch(bodies) if IngestionCapability.MARKDOWN_FACTS in requested else ()
    )
    for (offset, size, _), result in zip(texts, parsed, strict=True):
        path = paths[offset]
        relative = path.relative_to(root).as_posix()
        if isinstance(result, DocumentParseError):
            envelopes[offset] = _invalid(first_ordinal + offset, relative, result)
            continue
        envelopes[offset] = _envelope(
            first_ordinal + offset,
            path,
            relative,
            size,
            result,
            next(facts, None),
            requested,
        )
    yield from (envelope for envelope in envelopes if envelope is not None)


def _invalid(ordinal: int, relative: str, error: Exception) -> DocumentEnvelope:
    return DocumentEnvelope(
        ordinal=ordinal, path=relative, size=None, classification="invalid", error=str(error)
    )


def _envelope(  # noqa: PLR0913 - one document's already-computed parts
    ordinal: int,
    path: Path,
    relative: str,
    size: int,
    parsed: ParsedText,
    facts: MarkdownFacts | None,
    requested: frozenset[IngestionCapability],
) -> DocumentEnvelope:
    frontmatter = parsed.frontmatter
    classification = "reserved"
    if not is_reserved_document(path):
        concept_type = None if frontmatter is None else frontmatter.get("type")
        classification = (
            f"typed:{concept_type}" if isinstance(concept_type, str) and concept_type else "untyped"
        )
    wants_digests = IngestionCapability.DIGESTS in requested
    return DocumentEnvelope(
        ordinal=ordinal,
        path=relative,
        size=size,
        classification=classification,
        body=parsed.body if IngestionCapability.BODY in requested else None,
        frontmatter=frontmatter if IngestionCapability.FRONTMATTER in requested else None,
        facts=facts,
        source_digest=parsed.source_digest if wants_digests else None,
        parsed_digest=parsed.parsed_digest if wants_digests else None,
    )
