"""Application-facing concept lookup and frontmatter relation resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from okf_parser.bundle import Bundle


@dataclass(frozen=True, slots=True)
class ConceptView:
    """One parsed OKF concept exposed as a stable application-facing value object."""

    concept_id: str
    path: str
    concept_type: str | None
    title: str | None
    description: str | None
    frontmatter: dict[str, Any]
    body: str
    source_digest: str
    parsed_digest: str


def _rows(bundle: Bundle) -> list[dict[str, Any]]:
    """Materialize the concept relation once for bounded application-side lookup."""
    return bundle.concepts.execute().to_dict(orient="records")


def _normalized_ref(reference: str) -> tuple[str, str]:
    """Return candidate concept id and Markdown path for one bundle-relative reference."""
    raw = reference.strip().removeprefix("./").lstrip("/")
    path = PurePosixPath(raw)
    if path.suffix.lower() == ".md":
        concept_id = path.with_suffix("").as_posix()
        markdown_path = path.as_posix()
    else:
        concept_id = path.as_posix()
        markdown_path = f"{concept_id}.md"
    return concept_id, markdown_path


def concept(bundle: Bundle, reference: str) -> ConceptView:
    """Resolve one concept by bundle-relative concept id or Markdown path.

    ``reference`` is matched only against concepts already admitted by the loaded
    bundle. This avoids consumer-side filesystem traversal and reparsing.
    """
    concept_id, markdown_path = _normalized_ref(reference)
    matches = [
        row
        for row in _rows(bundle)
        if row["concept_id"] == concept_id or row["path"] == markdown_path
    ]
    if not matches:
        raise KeyError(f"OKF concept not found in bundle: {reference}")
    if len(matches) != 1:
        raise ValueError(f"OKF concept reference is ambiguous: {reference}")

    row = matches[0]
    frontmatter = json.loads(row["frontmatter_json"])
    if not isinstance(frontmatter, dict):
        raise ValueError(f"OKF concept frontmatter is not a mapping: {reference}")
    return ConceptView(
        concept_id=str(row["concept_id"]),
        path=str(row["path"]),
        concept_type=(str(row["concept_type"]) if row["concept_type"] is not None else None),
        title=(str(row["title"]) if row["title"] is not None else None),
        description=(str(row["description"]) if row["description"] is not None else None),
        frontmatter=frontmatter,
        body=str(row["body"]),
        source_digest=str(row["source_digest"]),
        parsed_digest=str(row["parsed_digest"]),
    )


def resolve_relations(
    bundle: Bundle,
    source: str | ConceptView,
    *,
    field: str = "sources",
    resource_key: str = "resource",
    target_type: str | None = None,
) -> list[ConceptView]:
    """Resolve local concept relations declared in a frontmatter list.

    The relation container is intentionally generic: consumers choose the
    frontmatter ``field`` and the key holding the target ``resource``. Remote or
    non-concept resources fail explicitly instead of being silently reinterpreted.
    ``target_type`` can restrict the returned concepts without teaching the parser
    any consumer-specific vocabulary.
    """
    source_concept = concept(bundle, source) if isinstance(source, str) else source
    relations = source_concept.frontmatter.get(field, [])
    if relations is None:
        return []
    if not isinstance(relations, list):
        raise ValueError(f"OKF relation field must be a list: {field}")

    resolved: list[ConceptView] = []
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            raise ValueError(f"OKF relation must be a mapping: {field}[{index}]")
        resource = relation.get(resource_key)
        if not isinstance(resource, str) or not resource.strip():
            raise ValueError(
                f"OKF relation must declare string {resource_key}: {field}[{index}]"
            )
        target = concept(bundle, resource)
        if target_type is None or target.concept_type == target_type:
            resolved.append(target)
    return resolved
