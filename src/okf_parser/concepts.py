"""Application-facing concept lookup and frontmatter relation resolution."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from okf_parser.models import ConceptRecord

if TYPE_CHECKING:
    from okf_parser.bundle import Bundle


def _normalized_ref(bundle: Bundle, reference: str | Path) -> tuple[str, str]:
    """Return candidate concept id and Markdown path for one in-bundle reference."""
    candidate = Path(reference)
    if candidate.is_absolute():
        try:
            raw = candidate.resolve().relative_to(bundle.root.resolve()).as_posix()
        except ValueError as exc:
            msg = f"OKF concept reference escapes bundle root: {reference}"
            raise ValueError(msg) from exc
    else:
        raw = str(reference).strip().replace("\\", "/").removeprefix("./")

    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        msg = f"OKF concept reference escapes bundle root: {reference}"
        raise ValueError(msg)
    if path.suffix.lower() == ".md":
        return path.with_suffix("").as_posix(), path.as_posix()
    concept_id = path.as_posix()
    return concept_id, f"{concept_id}.md"


def _index(bundle: Bundle) -> tuple[dict[str, ConceptRecord], dict[str, ConceptRecord]]:
    """Materialize parser-owned concept records once for deterministic lookup."""
    by_id: dict[str, ConceptRecord] = {}
    by_path: dict[str, ConceptRecord] = {}
    for row in bundle.concepts.execute().to_dict(orient="records"):
        record = ConceptRecord.model_validate(row)
        by_id[record.concept_id] = record
        by_path[record.path] = record
    return by_id, by_path


def _from_index(
    bundle: Bundle,
    reference: str | Path,
    by_id: dict[str, ConceptRecord],
    by_path: dict[str, ConceptRecord],
) -> ConceptRecord:
    concept_id, markdown_path = _normalized_ref(bundle, reference)
    record = by_id.get(concept_id) or by_path.get(markdown_path)
    if record is None:
        raise KeyError(f"OKF concept not found in bundle: {reference}")
    return record


def concept(bundle: Bundle, reference: str | Path) -> ConceptRecord:
    """Resolve one parser-owned concept by id or in-bundle Markdown path."""
    by_id, by_path = _index(bundle)
    return _from_index(bundle, reference, by_id, by_path)


def resolve_relations(
    bundle: Bundle,
    source: str | Path | ConceptRecord,
    *,
    field: str = "sources",
    resource_key: str = "resource",
    target_type: str | None = None,
) -> tuple[ConceptRecord, ...]:
    """Resolve local concepts referenced by a frontmatter relation list.

    Consumers choose the relation field, resource key and optional target type;
    this module owns only generic OKF lookup, shape validation and bundle
    containment. Domain meaning remains with the consumer.
    """
    by_id, by_path = _index(bundle)
    source_concept = (
        source if isinstance(source, ConceptRecord) else _from_index(bundle, source, by_id, by_path)
    )
    relations = source_concept.frontmatter.get(field, [])
    if relations is None:
        return ()
    if not isinstance(relations, list):
        raise ValueError(f"OKF relation field must be a list: {field}")

    resolved: list[ConceptRecord] = []
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            raise ValueError(f"OKF relation must be a mapping: {field}[{index}]")
        resource = relation.get(resource_key)
        if not isinstance(resource, str) or not resource.strip():
            raise ValueError(
                f"OKF relation must declare string {resource_key}: {field}[{index}]"
            )
        target = _from_index(bundle, resource, by_id, by_path)
        if target_type is None or target.concept_type == target_type:
            resolved.append(target)
    return tuple(resolved)
