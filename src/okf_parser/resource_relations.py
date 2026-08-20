"""Application-facing helpers for resolving OKF frontmatter resource relations."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from okf_parser.models import ConceptRecord, FRONTMATTER_ADAPTER, YamlValue

if TYPE_CHECKING:
    from okf_parser.bundle import Bundle


def _bundle_relative_path(bundle: Bundle, path: str | Path) -> str:
    """Normalize one concept path and reject paths outside the bundle root."""
    candidate = Path(path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        try:
            return resolved.relative_to(bundle.root.resolve()).as_posix()
        except ValueError as exc:
            msg = f"concept path escapes bundle root: {path}"
            raise ValueError(msg) from exc

    pure = PurePosixPath(str(path).replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        msg = f"concept path escapes bundle root: {path}"
        raise ValueError(msg)
    return pure.as_posix()


def concept_at(bundle: Bundle, path: str | Path) -> ConceptRecord:
    """Return one parsed concept by bundle-relative or in-bundle absolute path."""
    relative = _bundle_relative_path(bundle, path)
    rows = bundle.concepts.filter(bundle.concepts.path == relative).execute().to_dict(
        orient="records"
    )
    if not rows:
        msg = f"concept does not exist in bundle: {relative}"
        raise KeyError(msg)
    if len(rows) != 1:
        msg = f"concept path is not unique in bundle: {relative}"
        raise ValueError(msg)
    return ConceptRecord.model_validate(rows[0])


def concept_frontmatter(record: ConceptRecord) -> dict[str, YamlValue]:
    """Decode the parser-owned deterministic frontmatter representation."""
    parsed = json.loads(record.frontmatter_json)
    return FRONTMATTER_ADAPTER.validate_python(parsed, strict=True)


def resolve_resource_relations(
    bundle: Bundle,
    source: str | Path,
    *,
    field: str,
    target_type: str | None = None,
    require_nonempty: bool = False,
) -> tuple[ConceptRecord, ...]:
    """Resolve root-relative ``resource`` relations declared in frontmatter.

    This helper deliberately owns only generic OKF mechanics: locating the source
    concept, validating a relation-list shape, preventing bundle escape, resolving
    targets to parsed concepts, and optionally filtering by producer-defined type.
    Consumers remain responsible for domain meaning such as whether a field named
    ``sources`` represents editorial provenance.
    """
    source_record = concept_at(bundle, source)
    frontmatter = concept_frontmatter(source_record)
    relations = frontmatter.get(field)
    if relations is None:
        relations = []
    if not isinstance(relations, list):
        msg = f"frontmatter field {field!r} must be a list of resource relations"
        raise ValueError(msg)

    resolved: list[ConceptRecord] = []
    for relation in relations:
        if not isinstance(relation, dict):
            msg = f"frontmatter field {field!r} must contain resource mappings"
            raise ValueError(msg)
        resource = relation.get("resource")
        if not isinstance(resource, str) or not resource.strip():
            msg = f"frontmatter field {field!r} relation must declare resource"
            raise ValueError(msg)
        target = concept_at(bundle, resource)
        if target_type is None or target.concept_type == target_type:
            resolved.append(target)

    if require_nonempty and not resolved:
        suffix = f" of type {target_type!r}" if target_type is not None else ""
        msg = f"frontmatter field {field!r} has no resolvable resources{suffix}"
        raise ValueError(msg)
    return tuple(resolved)
