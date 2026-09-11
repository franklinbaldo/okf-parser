"""JSON-ready application services shared by CLI and MCP."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

import duckdb
import networkx as nx

from okf_parser.apply import apply_bundle as _apply_bundle
from okf_parser.bundle_import import import_bundle as _import_bundle
from okf_parser.classification import classify_path
from okf_parser.duckdb import attach_okf
from okf_parser.edit import preview_concept_edit as _preview_concept_edit
from okf_parser.edit import write_concept_edit as _write_concept_edit
from okf_parser.engine import load_bundle, validate_path
from okf_parser.formatting import FormatReport, format_path
from okf_parser.graphql_adapter import export_graphql_sdl
from okf_parser.schema_export import (
    RefsMode,
    export_json_schema,
    export_pydantic_source,
    export_zod_schema,
)
from okf_parser.schema_export import documents_by_type as _documents_by_type
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
    expected_preview_token: str | None = None,
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
        expected_preview_token=expected_preview_token,
    )


def check_bundle(
    path: str,
    exclude: Sequence[str] = (),
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
    relational_schema: str | None = None,
) -> dict[str, object]:
    """Validate every Markdown file below a path."""
    report = validate_path(
        Path(path),
        exclude,
        require_spec,
        normative_spec=normative_spec,
        relational_schema=Path(relational_schema) if relational_schema is not None else None,
    )
    return report.model_dump(mode="json")


def format_bundle(path: str, exclude: Sequence[str] = (), *, write: bool = False) -> dict[str, object]:
    """Format every Markdown file below a path."""
    report: FormatReport = format_path(Path(path), exclude=exclude, write=write)
    return report.model_dump(mode="json")


def preview_edit(
    path: str,
    concept: str,
    changes: dict[str, object],
) -> dict[str, object]:
    """Preview one concept edit without writing."""
    return _preview_concept_edit(path, concept, changes)


def write_edit(
    path: str,
    concept: str,
    changes: dict[str, object],
    expected_preview_token: str,
) -> dict[str, object]:
    """Write one concept edit guarded by the preview token."""
    return _write_concept_edit(path, concept, changes, expected_preview_token)


def classify(path: str) -> dict[str, object]:
    """Classify one document path."""
    return classify_path(Path(path)).model_dump(mode="json")


def inspect_bundle(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Return counts and diagnostics for one bundle."""
    bundle = load_bundle(Path(path), exclude)
    return {
        "root": str(bundle.root),
        "markdown_count": bundle.markdown_count,
        "concept_count": int(bundle.concepts.count().execute()),
        "reserved_count": int(bundle.reserved.count().execute()),
        "violations": [diagnostic.model_dump(mode="json") for diagnostic in bundle.diagnostics],
    }


def graph_bundle(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:
    """Return the bundle graph as JSON-ready nodes and edges."""
    graph = load_bundle(Path(path), exclude).graph()
    return {
        "nodes": [
            {"id": node, **attributes}
            for node, attributes in graph.nodes(data=True)
        ],
        "edges": [
            {"source": source, "target": target, **attributes}
            for source, target, attributes in graph.edges(data=True)
        ],
    }


def query_bundle(path: str, sql: str, exclude: Sequence[str] = ()) -> list[dict[str, object]]:
    """Run SQL over a loaded bundle through the DuckDB bridge."""
    connection = duckdb.connect()
    try:
        attach_okf(connection, load_bundle(Path(path), exclude))
        rows = connection.execute(sql).fetchdf()
        return rows.to_dict(orient="records")
    finally:
        connection.close()


def graphql_sdl(path: str, exclude: Sequence[str] = ()) -> str:
    """Export GraphQL SDL for the loaded bundle."""
    return export_graphql_sdl(load_bundle(Path(path), exclude))


def schema_json(
    path: str,
    exclude: Sequence[str] = (),
    *,
    refs: RefsMode = "inline",
) -> dict[str, object]:
    """Export JSON Schema for every observed concept type."""
    return export_json_schema(path, exclude=exclude, refs=refs)


def schema_pydantic(
    path: str,
    exclude: Sequence[str] = (),
    *,
    module_name: str = "okf_models",
) -> str:
    """Export Pydantic source for the bundle's observed schema."""
    return export_pydantic_source(path, exclude=exclude, module_name=module_name)


def schema_zod(
    path: str,
    exclude: Sequence[str] = (),
    *,
    module_name: str = "okfModels",
    zod_import: ZodImport = "zod",
) -> str:
    """Export Zod source for the bundle's observed schema."""
    return export_zod_schema(
        path,
        exclude=exclude,
        module_name=module_name,
        zod_import=zod_import,
    )


def apply(
    path: str,
    source: str,
    concept_type: str,
    *,
    id_column: str | None = None,
    write: bool = False,
    overwrite: bool = False,
    on_conflict: Literal["skip", "verify-identical"] = "skip",
    expected_preview_token: str | None = None,
) -> dict[str, object]:
    """Apply rows from a source as concept documents."""
    return _apply_bundle(
        path,
        source,
        concept_type,
        id_column=id_column,
        write=write,
        overwrite=overwrite,
        on_conflict=on_conflict,
        expected_preview_token=expected_preview_token,
    )
