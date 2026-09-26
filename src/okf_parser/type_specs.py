"""Derive the specification-document path of a producer-defined type.

The ``OKF010``/``OKF011`` rules that use this path run natively
(``okf-engine/src/specs.rs``); the slug is kept here only for the Python
writers that still name files by type until RFC 0024 phase 4 moves them.
Both implementations are pinned by ``conformance/type-spec-slugs.json``.

The path is *derived* rather than declared. A ``spec:`` frontmatter field would
be a second fact free to disagree with the first, and encoding the path in
``type`` itself would tie identity to layout, so renaming a directory would
invalidate every concept. Deriving keeps ``type`` a stable identity and makes
the document location computable.
"""

from __future__ import annotations

import re
import unicodedata

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
