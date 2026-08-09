"""JSON-ready application services shared by CLI and MCP."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

import duckdb
import networkx as nx

from okf_parser.apply import apply_bundle as _apply_bundle
from okf_parser.bundle import load_bundle, validate_path
from okf_parser.bundle_import import import_bundle as _import_bundle
from okf_parser.classification import classify_path
from okf_parser.duckdb import attach_okf
from okf_parser.edit import preview_concept_edit as _preview_concept_edit
from okf_parser.edit import write_concept_edit as _write_concept_edit
from okf_parser.formatting import FormatReport, format_path
from okf_parser.schema_export import documents_by_type as _documents_by_type
from okf_parser.schema_export import export_json_schema, export_pydantic_source, export_zod_schema
from okf_parser.spec_scaffold import scaffold_missing_declared_schemas, scaffold_missing_specs

if TYPE_CHECKING:
    from collections.abc import Sequence

    from okf_parser.schema_contract import ZodImport


def init_bundle(
    path: str,
    spec_template: str,
    exclude: Sequence[str] = (),
    *,
    write: bool = False,
    infer_schema: bool = False,
) -> dict[str, object]:
    """Scaffold a missing specification document, and optionally a starter `.schema.sql`.

    `infer_schema` runs the same `schema --infer-types` inference used
    elsewhere to propose a starter declaration for whichever types still
    lack a `.schema.sql`; it never touches a file that already exists.
    """
    root = Path(path).resolve()
    bundle = load_bundle(root, exclude)
    specs = scaffold_missing_specs(bundle.root, bundle.concept_types, spec_template, write=write)
    if not infer_schema:
        return {"specs": specs}
    observed = _documents_by_type(path, exclude)
    schemas = scaffold_missing_declared_schemas(bundle.root, spec_template, observed, write=write)
    return {"specs": specs, "schemas": schemas}


def import_bundle(  # each argument is an independent public CLI flag.
    source: str,
    path: str,
    concept_type: str,
    *,
    id_column: str | None = None,
    write: bool = False,
    overwrite: bool = False,
    on_conflict: Literal["skip", "verify-identical"] = "skip",
) -> dict[str, object]:
    """Materialize every row of a DuckDB-readable source as one concept document."""
    return _import_bundle(
        source,
        path,
        concept_type,
        id_column=id_column,
        write=write,
        overwrite=overwrite,
        on_conflict=on_conflict,
    )


def check_bundle(
    path: str,
    exclude: Sequence[str] = (),
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
    classify: bool = False,
) -> dict[str, object]:
    """Validate every Markdown file below a path."""
    report = validate_path(Path(path), exclude, require_spec, normative_spec=normative_spec)
    payload: dict[str, object] = {
        "root": str(report.root),
        "conformant": report.is_conformant,
        "markdown_count": report.markdown_count,
        "concept_count": report.concept_count,
        "reserved_count": report.reserved_count,
        "diagnostics": [item.model_dump(mode="json") for item in report.violations],
    }
    if classify:
        payload["classification"] = classify_path(Path(path), exclude)
    return payload


def inventory_bundle(
    path: str, exclude: Sequence[str] = (), *, digests: bool = False
) -> dict[str, object]:
    """Count concepts by their producer-defined type."""
    bundle = load_bundle(Path(path), exclude)
    rows = (
        bundle.concepts.group_by("concept_type")
        .aggregate(concept_count=lambda table: table.count())
        .order_by("concept_type")
        .execute()
        .to_dict(orient="records")
    )
    payload: dict[str, object] = {"root": str(bundle.root), "types": rows}
    if digests:
        payload["digests"] = (
            bundle.concepts.select("concept_id", "path", "source_digest", "parsed_digest")
            .order_by("path")
            .execute()
            .to_dict(orient="records")
        )
    return payload


def graph_bundle(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Summarize the resolved concept graph."""
    bundle = load_bundle(Path(path), exclude)
    graph = bundle.to_networkx()
    return {
        "root": str(bundle.root),
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "weakly_connected_components": nx.number_weakly_connected_components(graph),
        "strongly_connected_components": nx.number_strongly_connected_components(graph),
        "directed_acyclic": nx.is_directed_acyclic_graph(graph),
    }


def schema_bundle(  # service mirrors the independent public schema flags.
    path: str,
    fmt: str = "json",
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    zod_import: ZodImport = "zod",
    spec_template: str | None = None,
) -> dict[str, object] | str:
    """Export JSON Schema, Zod, or importable Pydantic source."""
    if fmt == "zod":
        return export_zod_schema(
            path,
            exclude,
            infer_types=infer_types,
            casts=casts,
            zod_import=zod_import,
            spec_template=spec_template,
        )
    if fmt == "pydantic":
        return export_pydantic_source(
            path,
            exclude,
            infer_types=infer_types,
            casts=casts,
            spec_template=spec_template,
        )
    return export_json_schema(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
    )


def _format_payload(report: FormatReport) -> dict[str, object]:
    return {
        "markdown_count": report.markdown_count,
        "clean": report.clean,
        "changed_paths": list(report.changed_paths),
        "skipped_paths": list(report.skipped_paths),
        "succeeded": report.succeeded,
        "written": report.written,
    }


def check_format(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Check mdformat style without modifying files."""
    return _format_payload(format_path(Path(path), exclude=exclude))


def write_format(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Explicitly rewrite Markdown files into canonical form."""
    return _format_payload(format_path(Path(path), write=True, exclude=exclude))


def apply_bundle(  # each argument is an independent public CLI flag.
    path: str,
    *,
    sql: str | None = None,
    type_name: str | None = None,
    field_name: str | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
    write: bool = False,
    exclude: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, object]:
    """Mutate frontmatter fields across a bundle via a bounded SQL script."""
    return _apply_bundle(
        path,
        sql=sql,
        type_name=type_name,
        field_name=field_name,
        from_value=from_value,
        to_value=to_value,
        write=write,
        exclude=exclude,
        spec_template=spec_template,
    )


def preview_concept_edit(
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Preview one conflict-safe Markdown body replacement."""
    return _preview_concept_edit(path, concept_id, body, expected_source_digest, exclude=exclude)


def write_concept_edit(
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Commit one conflict-safe Markdown body replacement."""
    return _write_concept_edit(path, concept_id, body, expected_source_digest, exclude=exclude)


def export_duckdb(
    path: str,
    database: str,
    schema: str = "okf",
    *,
    overwrite: bool = False,
    exclude: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, object]:
    """Materialize an OKF bundle into a DuckDB database file."""
    connection = duckdb.connect(database)
    try:
        result = attach_okf(
            connection,
            path,
            schema=schema,
            overwrite=overwrite,
            exclude=exclude,
            spec_template=spec_template,
        )
    finally:
        connection.close()
    return {**result, "database": str(Path(database).resolve())}
