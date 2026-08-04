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

import re
import unicodedata
from typing import TYPE_CHECKING

from okf_parser.models import Severity, Violation

if TYPE_CHECKING:
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
