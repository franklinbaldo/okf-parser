"""Scaffold starter `.schema.sql` declarations (`init --infer-schema`).

The specification stubs themselves are scaffolded natively
(`okf-engine/src/specs.rs`, `__init-specs`). What stays here is the
DuckDB-backed inference of starter columns, which moves with the rest of
DuckDB in RFC 0024 phase 4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.declared_schema import (
    StarterKind,
    declared_schema_relative_path,
    infer_kinds_via_duckdb,
    render_starter_schema_sql,
)

if TYPE_CHECKING:
    from pathlib import Path


def _starter_columns(documents: list[dict[str, object]]) -> dict[str, StarterKind]:
    """Infer conservative top-level physical columns from authored frontmatter.

    Scalars keep the existing DuckDB TRY_CAST inference. A field whose every
    non-null observation is a mapping or list becomes DuckDB JSON, preserving
    arbitrary nested shape without pretending sparse examples define a complete
    STRUCT. Mixed scalar/structured fields remain omitted.
    """
    field_names: set[str] = set()
    structured_seen: set[str] = set()
    scalar_seen: set[str] = set()
    for document in documents:
        for key, value in document.items():
            if key == "type":
                continue
            field_names.add(key)
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                structured_seen.add(key)
            else:
                scalar_seen.add(key)

    def cell(value: object) -> str | None:
        if value is None:
            return None
        return value if isinstance(value, str) else str(value)

    columns_by_name = {
        name: [cell(document.get(name)) for document in documents]
        for name in sorted(field_names - structured_seen)
    }
    inferred: dict[str, StarterKind] = dict(infer_kinds_via_duckdb(columns_by_name))
    for name in sorted(structured_seen - scalar_seen):
        inferred[name] = "json"
    return inferred


def _schema_plan(
    root: Path, spec_template: str, documents_by_type: dict[str, list[dict[str, object]]]
) -> tuple[dict[str, tuple[str, dict[str, StarterKind]]], dict[str, list[str]]]:
    by_path: dict[str, list[str]] = {}
    for concept_type in documents_by_type:
        relative = declared_schema_relative_path(spec_template, concept_type)
        if relative is None:
            continue
        by_path.setdefault(relative, []).append(concept_type)

    collisions = {path: types for path, types in by_path.items() if len(types) > 1}
    to_create: dict[str, tuple[str, dict[str, StarterKind]]] = {}
    for relative, types in by_path.items():
        if relative in collisions or (root / relative).is_file():
            continue
        concept_type = types[0]
        columns = _starter_columns(documents_by_type[concept_type])
        if columns:
            to_create[concept_type] = (relative, columns)
    return to_create, collisions


def scaffold_missing_declared_schemas(
    root: Path,
    spec_template: str,
    documents_by_type: dict[str, list[dict[str, object]]],
    *,
    write: bool = False,
) -> dict[str, object]:
    """Write a starter `.schema.sql` (RFC 0006 decision 11's flagged follow-up), inferred.

    Each column's type comes from DuckDB's own `TRY_CAST` over that field's
    observed values (`infer_kind_via_duckdb`) - the same all-or-nothing test
    decision 5 uses to *check* a declared column, reused here to *propose*
    one - and only for a type where at least one scalar field survived that
    test. Existing files are never overwritten, and a derived-path collision
    blocks the whole call, matching `scaffold_missing_specs`.
    """
    to_create, collisions = _schema_plan(root, spec_template, documents_by_type)
    if collisions:
        return {
            "created": [],
            "would_create": [],
            "collisions": [
                {"path": path, "types": sorted(types)} for path, types in sorted(collisions.items())
            ],
            "written": False,
        }
    if not write:
        return {
            "created": [],
            "would_create": sorted(relative for relative, _ in to_create.values()),
            "collisions": [],
            "written": False,
        }
    created: list[str] = []
    for concept_type, (relative, columns) in to_create.items():
        content = render_starter_schema_sql(concept_type, columns)
        if content is None:
            continue
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        created.append(relative)
    return {"created": sorted(created), "would_create": [], "collisions": [], "written": True}
