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
    is_reserved_document,
    markdown_facts,
    split_optional_frontmatter,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from okf_parser.models import YamlValue


class IngestionCapability(StrEnum):
    """Per-document work requested by an ingestion consumer."""

    IDENTITY = "identity"
    BODY = "body"
    FRONTMATTER = "frontmatter"
    MARKDOWN_FACTS = "markdown_facts"


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

    for ordinal, path in enumerate(paths):
        relative = path.relative_to(root).as_posix()
        classification = "reserved" if is_reserved_document(path) else "candidate"
        try:
            size = path.stat().st_size
            if not needs_text:
                yield DocumentEnvelope(ordinal, relative, size, classification)
                continue

            text = path.read_text(encoding="utf-8")
            frontmatter, body = split_optional_frontmatter(text)
            if classification != "reserved":
                concept_type = None if frontmatter is None else frontmatter.get("type")
                classification = (
                    f"typed:{concept_type}"
                    if isinstance(concept_type, str) and concept_type
                    else "untyped"
                )
            facts = (
                markdown_facts(body) if IngestionCapability.MARKDOWN_FACTS in requested else None
            )
            yield DocumentEnvelope(
                ordinal=ordinal,
                path=relative,
                size=size,
                classification=classification,
                body=body if IngestionCapability.BODY in requested else None,
                frontmatter=(frontmatter if IngestionCapability.FRONTMATTER in requested else None),
                facts=facts,
            )
        except (DocumentParseError, OSError, UnicodeDecodeError) as exc:
            yield DocumentEnvelope(
                ordinal=ordinal,
                path=relative,
                size=None,
                classification="invalid",
                error=str(exc),
            )
