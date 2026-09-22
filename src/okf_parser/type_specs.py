"""Require every producer-defined type in use to have a specification document.

OKF v0.2 only requires ``type`` to be non-empty, so a producer can invent a
type, emit concepts of it and keep a green ``check`` while the frontmatter
schema of that type changes underneath its consumers. The rule here closes
that gap without inventing taxonomy: it derives a document path from the type
name and reports the types whose document is absent.

The path is *derived* rather than declared. A ``spec:`` frontmatter field would
be a second fact free to disagree with the first, and encoding the path in
``type`` itself would tie identity to layout, so renaming a directory would
invalidate every concept. Deriving keeps ``type`` a stable identity and makes
the document location computable.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import TYPE_CHECKING

from okf_parser.models import Severity, Violation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

SPEC_SLUG_PLACEHOLDER = "{slug}"
_SEPARATOR_RE = re.compile(r"[\s/]+")
_REMOVED_RE = re.compile(r"[^a-z0-9-]+")
_HYPHEN_RUN_RE = re.compile(r"-{2,}")


class SpecTemplateError(ValueError):
    """Raised when a specification path template cannot address a type."""


def type_slug(concept_type: str) -> str:
    """Derive the filesystem-safe slug of one producer-defined type.

    Accents and cedillas are removed, whitespace and ``/`` become hyphens, and
    every remaining non-alphanumeric character is dropped.
    """
    decomposed = unicodedata.normalize("NFKD", concept_type)
    stripped = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    hyphenated = _SEPARATOR_RE.sub("-", stripped.strip().lower())
    slug = _HYPHEN_RUN_RE.sub("-", _REMOVED_RE.sub("", hyphenated))
    return slug.strip("-")


def spec_relative_path(template: str, concept_type: str) -> str | None:
    """Render the bundle-relative document path expected for one type.

    Returns ``None`` when the type has no slug at all, which a script or a
    non-Latin script name can produce. That is a bundle fact, so it becomes a
    diagnostic; only an unusable template is an operator error.
    """
    if SPEC_SLUG_PLACEHOLDER not in template:
        message = f"specification template must contain {SPEC_SLUG_PLACEHOLDER}: {template!r}"
        raise SpecTemplateError(message)
    slug = type_slug(concept_type)
    if not slug:
        return None
    return template.replace(SPEC_SLUG_PLACEHOLDER, slug)


def missing_type_specs(
    root: Path,
    concept_types: set[str],
    template: str,
    *,
    normative: bool = False,
) -> list[Violation]:
    """Report every type in use whose derived specification document is absent.

    The diagnostic is advisory by default: a bundle mid-adoption legitimately
    has legacy types without a document, and that is not an OKF v0.2 defect.
    """
    severity = Severity.ERROR if normative else Severity.WARNING
    violations: list[Violation] = []
    for concept_type in sorted(concept_types):
        if not concept_type:
            continue
        relative = spec_relative_path(template, concept_type)
        if relative is None:
            violations.append(
                Violation(
                    code="OKF010",
                    severity=severity,
                    path=template,
                    message=f'type "{concept_type}" has no slug usable as a document path',
                )
            )
            continue
        if (root / relative).is_file():
            continue
        violations.append(
            Violation(
                code="OKF010",
                severity=severity,
                path=relative,
                message=f'type "{concept_type}" has no specification document',
            )
        )
    return violations


def _required_fields_from_spec(path: Path) -> tuple[str, ...]:
    """Read the first column of a Markdown `## Required fields` table.

    Type specifications stay ordinary Markdown. This intentionally recognizes
    only the small, explicit contract already used by OKF producer specs:
    a level-two `Required fields` section followed by a pipe table whose first
    column names the authored frontmatter field.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ()

    lines = text.splitlines()
    in_required = False
    fields: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip().casefold()
            if in_required and heading != "required fields":
                break
            in_required = heading == "required fields"
            continue
        if not in_required or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        raw = cells[0].strip()
        if not raw or raw.casefold() == "field":
            continue
        if set(raw) <= {"-", ":"}:
            continue
        field = raw.strip("`").strip()
        if field and field not in fields:
            fields.append(field)
    return tuple(fields)


def _required_fields_for_type(
    root: Path,
    template: str,
    concept_type: str,
) -> tuple[str, ...]:
    """Resolve the authored required fields for one concept type."""
    relative = spec_relative_path(template, concept_type)
    if relative is None:
        return ()
    spec_path = root / relative
    if not spec_path.is_file():
        return ()
    return _required_fields_from_spec(spec_path)


def required_type_spec_fields(
    root: Path,
    concepts: Sequence[Mapping[str, object]],
    template: str,
    *,
    normative: bool = False,
) -> list[Violation]:
    """Validate authored frontmatter against required fields declared by its type spec.

    The rule is advisory with `--require-spec` and normative with
    `--normative-spec`, matching the existing missing-spec policy.
    """
    severity = Severity.ERROR if normative else Severity.WARNING
    required_by_type: dict[str, tuple[str, ...]] = {}
    violations: list[Violation] = []

    for record in concepts:
        concept_type = record.get("concept_type")
        path = record.get("path")
        frontmatter_json = record.get("frontmatter_json")
        if not isinstance(concept_type, str) or not concept_type:
            continue
        if not isinstance(path, str) or not isinstance(frontmatter_json, str):
            continue

        required = required_by_type.get(concept_type)
        if required is None:
            required = _required_fields_for_type(root, template, concept_type)
            required_by_type[concept_type] = required
        if not required:
            continue

        try:
            frontmatter = json.loads(frontmatter_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(frontmatter, dict):
            continue

        for field in required:
            value = frontmatter.get(field)
            missing = (
                field not in frontmatter
                or value is None
                or (isinstance(value, str) and not value.strip())
            )
            if not missing:
                continue
            violations.append(
                Violation(
                    code="OKF011",
                    severity=severity,
                    path=path,
                    message=(
                        f'type "{concept_type}" requires non-empty frontmatter field "{field}"'
                    ),
                )
            )
    return violations
