"""JSON-ready application services shared by CLI and MCP."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from okf_parser.apply import apply_bundle as _apply_bundle
from okf_parser.bundle import check_report
from okf_parser.bundle_import import import_bundle as _import_bundle
from okf_parser.declared_schema import DeclaredSchemaError
from okf_parser.edit import preview_concept_edit as _preview_concept_edit
from okf_parser.edit import write_concept_edit as _write_concept_edit
from okf_parser.formatting import FormatReport, format_path
from okf_parser.graphql_adapter import export_graphql_sdl
from okf_parser.rust_core import native_result
from okf_parser.schema_export import (
    RefsMode,
    export_json_schema,
    export_pydantic_source,
    export_zod_schema,
)
from okf_parser.type_specs import SpecTemplateError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from okf_parser.schema_contract import ZodImport


class _InitRequest(BaseModel):
    """The ``__init`` request the binary validates."""

    model_config = ConfigDict(frozen=True)

    path: str
    spec_template: str
    exclude: list[str]
    write: bool
    infer_schema: bool


def init_bundle(
    path: str,
    spec_template: str,
    exclude: Sequence[str] = (),
    *,
    write: bool = False,
    infer_schema: bool = False,
) -> dict[str, JsonValue]:
    """Scaffold missing specification documents, and optionally starter `.schema.sql`s.

    `infer_schema` proposes a starter declaration, typed from the values the
    documents use, for each type that still lacks one; existing files are
    never touched.
    """
    return native_result(
        "__init",
        _InitRequest(
            path=str(Path(path).resolve()),
            spec_template=spec_template,
            exclude=list(exclude),
            write=write,
            infer_schema=infer_schema,
        ),
        {"spec_template": SpecTemplateError},
    )


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
    """Validate a bundle, optionally with its declared relations, as the CLI reports it."""
    report = check_report(
        Path(path),
        exclude,
        require_spec,
        normative_spec=normative_spec,
        classify=classify,
        relational_schema=None if relational_schema is None else Path(relational_schema),
    )
    return report.model_dump(mode="json", exclude_none=True)


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
) -> dict[str, JsonValue]:
    """Edit frontmatter fields with SQL; see :func:`okf_parser.apply.apply_bundle`."""
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


class BundleExportError(ValueError):
    """Raised when an export would replace tables and ``overwrite`` is off."""

    def __init__(self, message: str, schema_name: str, tables: tuple[str, ...]) -> None:
        """Record which schema already holds which of the bundle's tables."""
        self.schema_name = schema_name
        self.tables = tables
        super().__init__(message)


class _ExportRequest(BaseModel):
    """The ``__export-duckdb`` request the binary validates."""

    model_config = ConfigDict(frozen=True, serialize_by_alias=True)

    path: str
    database: str
    schema_name: str = Field(serialization_alias="schema")
    overwrite: bool
    exclude: list[str]
    spec_template: str | None


class _ExportRefusal(BaseModel):
    error: str
    schema_name: str = Field(alias="schema")
    existing_tables: tuple[str, ...]


def export_duckdb(
    path: str,
    database: str,
    schema: str = "okf",
    *,
    overwrite: bool = False,
    exclude: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, JsonValue]:
    """Materialize an OKF bundle into a DuckDB database file.

    ``concepts``, ``links``, ``reserved`` and ``diagnostics`` land in
    ``schema``; with ``spec_template``, each declared type gets a typed table
    in ``{schema}_types``. Existing tables are replaced only with
    ``overwrite``; otherwise :class:`BundleExportError` names them.
    """
    result = native_result(
        "__export-duckdb",
        _ExportRequest(
            path=str(Path(path).resolve()),
            database=database,
            schema_name=schema,
            overwrite=overwrite,
            exclude=list(exclude),
            spec_template=spec_template,
        ),
        {"spec_template": SpecTemplateError, "declared_schema": DeclaredSchemaError},
    )
    if "existing_tables" in result:
        refusal = _ExportRefusal.model_validate(result)
        raise BundleExportError(refusal.error, refusal.schema_name, refusal.existing_tables)
    return result
