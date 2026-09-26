"""JSON-ready application services shared by CLI and MCP."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

import duckdb
from pydantic import BaseModel, ConfigDict

from okf_parser.apply import apply_bundle as _apply_bundle
from okf_parser.bundle import check_report, load_bundle
from okf_parser.bundle_import import import_bundle as _import_bundle
from okf_parser.duckdb import attach_okf
from okf_parser.edit import preview_concept_edit as _preview_concept_edit
from okf_parser.edit import write_concept_edit as _write_concept_edit
from okf_parser.formatting import FormatReport, format_path
from okf_parser.graphql_adapter import export_graphql_sdl
from okf_parser.models import Severity
from okf_parser.relational_schema import validate_relations
from okf_parser.rust_core import RustCoreError, call_native
from okf_parser.schema_export import (
    RefsMode,
    export_json_schema,
    export_pydantic_source,
    export_zod_schema,
)
from okf_parser.schema_export import documents_by_type as _documents_by_type
from okf_parser.spec_scaffold import scaffold_missing_declared_schemas
from okf_parser.type_specs import SpecTemplateError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from okf_parser.schema_contract import ZodImport


class _InitSpecsRequest(BaseModel):
    """The ``__init-specs`` request the binary validates."""

    model_config = ConfigDict(frozen=True)

    path: str
    spec_template: str
    exclude: list[str]
    write: bool


def _native_init_specs(
    path: str, spec_template: str, exclude: Sequence[str], *, write: bool
) -> dict[str, JsonValue]:
    response = call_native(
        "__init-specs",
        _InitSpecsRequest(
            path=str(Path(path).resolve()),
            spec_template=spec_template,
            exclude=list(exclude),
            write=write,
        ),
    )
    if response.error is not None:
        if response.error.kind == "spec_template":
            raise SpecTemplateError(response.error.message)
        if response.error.kind == "request":
            raise ValueError(response.error.message)
        raise RustCoreError(response.error.message)
    if response.result is None:
        msg = "okf-parser __init-specs answered with neither a result nor an error"
        raise RustCoreError(msg)
    return response.result


def init_bundle(
    path: str,
    spec_template: str,
    exclude: Sequence[str] = (),
    *,
    write: bool = False,
    infer_schema: bool = False,
) -> dict[str, object]:
    """Scaffold a missing specification document, and optionally a starter `.schema.sql`.

    The specification stubs are scaffolded natively; `infer_schema` adds the
    DuckDB-backed `schema --infer-types` inference that proposes a starter
    declaration for whichever types still lack a `.schema.sql`, never
    touching a file that already exists.
    """
    specs = _native_init_specs(path, spec_template, exclude, write=write)
    if not infer_schema:
        return {"specs": specs}
    root = Path(path).resolve()
    observed = _documents_by_type(path, exclude)
    schemas = scaffold_missing_declared_schemas(root, spec_template, observed, write=write)
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
    classify: bool = False,
    relational_schema: str | None = None,
) -> dict[str, object]:
    """Validate a bundle with declared relations (`check --relational-schema`).

    Every other check runs natively; this adds the DuckDB-backed relational
    diagnostics to the native report until RFC 0024 phase 4.
    """
    report = check_report(
        Path(path),
        exclude,
        require_spec,
        normative_spec=normative_spec,
        classify=classify,
    )
    diagnostics = list(report.diagnostics)
    if relational_schema is not None:
        schema = Path(relational_schema)
        schema_path = schema if schema.is_absolute() else report.root / schema
        diagnostics.extend(validate_relations(load_bundle(report.root, exclude), schema_path))
    diagnostics.sort(key=lambda item: (item.path, item.severity.value, item.code, item.message))
    payload: dict[str, object] = {
        "root": str(report.root),
        "conformant": not any(item.severity is Severity.ERROR for item in diagnostics),
        "markdown_count": report.markdown_count,
        "concept_count": report.concept_count,
        "reserved_count": report.reserved_count,
        "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
    }
    if report.classification is not None:
        payload["classification"] = report.classification.model_dump(mode="json")
    return payload


def schema_bundle(  # service mirrors the independent public schema flags.
    path: str,
    fmt: str = "json",
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    zod_import: ZodImport = "zod",
    spec_template: str | None = None,
    relational_schema: str | None = None,
    refs: RefsMode = "key",
) -> dict[str, object] | str:
    """Export JSON Schema, Zod, Pydantic source, or deterministic GraphQL SDL.

    `relational_schema` points at the bundle's `okf.schema.sql`; with it, a
    field participating in a declared foreign key exports as a reference.
    GraphQL keeps its own shape and ignores the flag for now.
    """
    if fmt == "graphql":
        return export_graphql_sdl(
            path,
            exclude,
            infer_types=infer_types,
            casts=casts,
            spec_template=spec_template,
        )
    if fmt == "zod":
        return export_zod_schema(
            path,
            exclude,
            relational_schema=relational_schema,
            refs=refs,
            infer_types=infer_types,
            casts=casts,
            zod_import=zod_import,
            spec_template=spec_template,
        )
    if fmt == "pydantic":
        return export_pydantic_source(
            path,
            exclude,
            relational_schema=relational_schema,
            refs=refs,
            infer_types=infer_types,
            casts=casts,
            spec_template=spec_template,
        )
    return export_json_schema(
        path,
        exclude,
        relational_schema=relational_schema,
        refs=refs,
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
    expected_preview_token: str | None = None,
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
        expected_preview_token=expected_preview_token,
    )


def preview_concept_edit(
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    exclude: Sequence[str] = (),
) -> dict[str, JsonValue]:
    """Preview one conflict-safe Markdown body replacement."""
    return _preview_concept_edit(path, concept_id, body, expected_source_digest, exclude=exclude)


def write_concept_edit(
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    exclude: Sequence[str] = (),
) -> dict[str, JsonValue]:
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
