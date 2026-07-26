"""Small immutable domain records used by the parser and validator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class Severity(StrEnum):
    """A diagnostic's effect on conformance."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Violation:
    """One deterministic bundle diagnostic."""

    code: str
    severity: Severity
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ConceptRecord:
    """One parsed OKF concept document."""

    concept_id: str
    logical_key: str
    path: str
    concept_type: str
    title: str | None
    description: str | None
    frontmatter_json: str
    body: str


@dataclass(frozen=True, slots=True)
class ReservedRecord:
    """One reserved index.md or log.md document."""

    path: str
    filename: str
    body: str


@dataclass(frozen=True, slots=True)
class LinkRecord:
    """One local Markdown relationship originating in a concept."""

    source_id: str
    raw_target: str
    target_id: str | None
    exists: bool
    origin: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Result of parsing one concept without applying semantic rules."""

    path: Path
    frontmatter: dict[str, object]
    body: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Aggregate result for every Markdown file below one path."""

    root: Path
    markdown_count: int
    concept_count: int
    reserved_count: int
    violations: tuple[Violation, ...]

    @property
    def is_conformant(self) -> bool:
        """Whether every Markdown file satisfies its OKF v0.2 rules."""
        return not any(item.severity is Severity.ERROR for item in self.violations)
