"""Export canonical JSON Schema, Zod, and Pydantic schemas for OKF frontmatter.

The binary compiles the contracts and renders every text format
(``okf-db/src/schema``); Pydantic models and source are built here from the
contracts it returns.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from okf_parser.pydantic_projection import (
    build_dynamic_pydantic_models,
    render_pydantic_source,
)
from okf_parser.relational_schema import RelationalSchemaError
from okf_parser.rust_core import ErrorKind, native_result
from okf_parser.schema_contract import (
    ProjectionError,
    RefsMode,
    SchemaCastError,
    SchemaExportError,
    SchemaNameCollisionError,
    SchemaReferenceError,
    TypeContract,
    ZodImport,
    contracts_from_json,
)
from okf_parser.type_specs import SpecTemplateError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


type SchemaTarget = Literal["contracts", "json", "zod", "graphql"]


class SchemaRequest(BaseModel):
    """One ``__schema`` request: a bundle, its compile options, and a target."""

    model_config = ConfigDict(frozen=True)

    path: str
    target: SchemaTarget
    exclude: tuple[str, ...] = ()
    infer_types: bool = False
    casts: tuple[str, ...] = ()
    zod_import: ZodImport = "zod"
    spec_template: str | None = None
    relational_schema: str | None = None
    refs: RefsMode = "key"


SCHEMA_ERRORS: Mapping[ErrorKind, Callable[[str], Exception]] = {
    "io": SchemaExportError,
    "schema_export": SchemaExportError,
    "schema_cast": SchemaCastError,
    "schema_name_collision": SchemaNameCollisionError,
    "schema_reference": SchemaReferenceError,
    "projection": ProjectionError,
    "spec_template": SpecTemplateError,
    "relational_schema": RelationalSchemaError,
}


def native_schema(
    request: SchemaRequest,
    errors: Mapping[ErrorKind, Callable[[str], Exception]] = SCHEMA_ERRORS,
) -> dict[str, JsonValue]:
    """Answer one ``__schema`` request, raising the schema error it names."""
    return native_result("__schema", request, errors)


def build_schema_contracts(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    spec_template: str | None = None,
    relational_schema: str | None = None,
    refs: RefsMode = "key",
) -> tuple[TypeContract, ...]:
    """Compile observations and declared schemas into deterministic contracts.

    A type with a declared ``.schema.sql`` beside its authored specification is
    exportable even before the bundle contains its first concrete document.

    `relational_schema` is the opt-in half: given the bundle's `okf.schema.sql`
    (relative to the bundle root, or absolute), every field participating in a
    declared foreign key compiles to a reference node. Projection documents
    compose a root contract with named sibling-schema references; they never
    become concept types themselves.
    """
    result = native_schema(
        SchemaRequest(
            path=path,
            target="contracts",
            exclude=tuple(exclude),
            infer_types=infer_types,
            casts=tuple(casts),
            spec_template=spec_template,
            relational_schema=relational_schema,
            refs=refs,
        )
    )
    return contracts_from_json(result)


def build_pydantic_models(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    spec_template: str | None = None,
    relational_schema: str | None = None,
    refs: RefsMode = "key",
) -> dict[str, type[BaseModel]]:
    """Build dynamic Pydantic adapters from the shared schema contracts."""
    contracts = build_schema_contracts(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
        relational_schema=relational_schema,
        refs=refs,
    )
    return build_dynamic_pydantic_models(contracts)


def export_pydantic_source(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    spec_template: str | None = None,
    relational_schema: str | None = None,
    refs: RefsMode = "key",
) -> str:
    """Generate deterministic importable Pydantic v2 source."""
    contracts = build_schema_contracts(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
        relational_schema=relational_schema,
        refs=refs,
    )
    return render_pydantic_source(contracts)


def export_json_schema(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    spec_template: str | None = None,
    relational_schema: str | None = None,
    refs: RefsMode = "key",
) -> dict[str, Any]:
    """Export the canonical JSON Schema representation for each concept type."""
    return native_schema(
        SchemaRequest(
            path=str(Path(path)),
            target="json",
            exclude=tuple(exclude),
            infer_types=infer_types,
            casts=tuple(casts),
            spec_template=spec_template,
            relational_schema=relational_schema,
            refs=refs,
        )
    )


def export_zod_schema(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    zod_import: ZodImport = "zod",
    spec_template: str | None = None,
    relational_schema: str | None = None,
    refs: RefsMode = "key",
) -> str:
    """Generate canonical Zod declarations, using generic Zod by default."""
    result = native_schema(
        SchemaRequest(
            path=path,
            target="zod",
            exclude=tuple(exclude),
            infer_types=infer_types,
            casts=tuple(casts),
            zod_import=zod_import,
            spec_template=spec_template,
            relational_schema=relational_schema,
            refs=refs,
        )
    )
    text = result.get("text")
    if not isinstance(text, str):
        message = "okf-parser __schema answered zod without text"
        raise SchemaExportError(message)
    return text


__all__ = [
    "ProjectionError",
    "SchemaCastError",
    "SchemaExportError",
    "SchemaNameCollisionError",
    "SchemaReferenceError",
    "build_pydantic_models",
    "build_schema_contracts",
    "export_json_schema",
    "export_pydantic_source",
    "export_zod_schema",
]
