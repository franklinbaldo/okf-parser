"""Small immutable domain records used by the parser and validator."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter

type YamlValue = str | None | list[YamlValue] | dict[str, YamlValue]
"""OKF frontmatter with mapping/list structure and scalar spelling preserved."""

FRONTMATTER_ADAPTER: TypeAdapter[dict[str, YamlValue]] = TypeAdapter(dict[str, YamlValue])
"""Validates a loaded YAML mapping before any other code touches it."""


class Severity(StrEnum):
    """A diagnostic's effect on conformance."""

    ERROR = "error"
    WARNING = "warning"


class Violation(BaseModel):
    """One deterministic bundle diagnostic."""

    model_config = ConfigDict(frozen=True)

    code: str
    severity: Severity
    path: str
    message: str


class ConceptRecord(BaseModel):
    """One parsed OKF concept document."""

    model_config = ConfigDict(frozen=True)

    concept_id: str
    logical_key: str
    path: str
    concept_type: str
    title: str | None
    description: str | None
    source_digest: str
    parsed_digest: str
    frontmatter_json: str
    body: str

    @property
    def frontmatter(self) -> dict[str, YamlValue]:
        """Return the validated structured frontmatter represented by this record."""
        return FRONTMATTER_ADAPTER.validate_json(self.frontmatter_json, strict=True)


class ReservedRecord(BaseModel):
    """One reserved index.md or log.md document."""

    model_config = ConfigDict(frozen=True)

    path: str
    filename: str
    body: str


class LinkRecord(BaseModel):
    """One local Markdown relationship originating in a concept."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    raw_target: str
    target_id: str | None
    exists: bool
    origin: str


class ParsedDocument(BaseModel):
    """Result of parsing one concept without applying semantic rules."""

    model_config = ConfigDict(frozen=True)

    path: Path
    frontmatter: dict[str, YamlValue]
    body: str
    source_digest: str
    parsed_digest: str

    @property
    def concept_type(self) -> str:
        """The normalized producer-defined type, empty when absent or not a string."""
        value = self.frontmatter.get("type")
        return value.strip() if isinstance(value, str) else ""

    @property
    def title(self) -> str | None:
        """The optional human-readable title."""
        return self._string_field("title")

    @property
    def description(self) -> str | None:
        """The optional human-readable description."""
        return self._string_field("description")

    @property
    def frontmatter_json(self) -> str:
        """Deterministic JSON for the preserved frontmatter."""
        return json.dumps(self.frontmatter, ensure_ascii=False, sort_keys=True)

    def _string_field(self, field: str) -> str | None:
        value = self.frontmatter.get(field)
        return value if isinstance(value, str) else None


class ValidationReport(BaseModel):
    """Aggregate result for every Markdown file below one path."""

    model_config = ConfigDict(frozen=True)

    root: Path
    markdown_count: int
    concept_count: int
    reserved_count: int
    violations: tuple[Violation, ...]

    @property
    def is_conformant(self) -> bool:
        """Whether every Markdown file satisfies its OKF v0.2 rules."""
        return not any(item.severity is Severity.ERROR for item in self.violations)
