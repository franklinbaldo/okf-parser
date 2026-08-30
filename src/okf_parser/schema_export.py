"""Export canonical JSON Schema, Zod, and Pydantic schemas for OKF frontmatter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from okf_parser.bundle import load_bundle
from okf_parser.declared_schema import (
    DeclaredSchemaError,
    declared_schema_relative_path,
    parse_declared_schema,
)
from okf_parser.projection_export import compile_projections
from okf_parser.projections import PROJECTION_TYPE, load_projections
from okf_parser.pydantic_projection import (
    build_dynamic_pydantic_models,
    render_pydantic_source,
)
from okf_parser.relational_schema import load_relational_schema
from okf_parser.schema_contract import (
    ListNode,
    ObjectNode,
    RefNode,
    SchemaCastError,
    SchemaExportError,
    SchemaNameCollisionError,
    TypeContract,
    ZodImport,
    compile_contracts,
    contract_json_schema,
    render_zod,
)
from okf_parser.schema_references import apply_references

type RefsMode = Literal["key", "embed"]

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

    from okf_parser.duckdb_types import DuckDBLogicalType


def _restore_projection_booleans(frontmatter: dict[str, object]) -> None:
    """Restore bools lost by the bundle's scalar-normalized frontmatter view."""
    include = frontmatter.get("include")
    if not isinstance(include, list):
        return
    for member in include:
        if not isinstance(member, dict):
            continue
        optional = member.get("optional")
        if optional == "true":
            member["optional"] = True
        elif optional == "false":
            member["optional"] = False


def documents_by_type(
    path: str,
    exclude: Sequence[str],
) -> dict[str, list[dict[str, object]]]:
    """Every concept's raw frontmatter, grouped by its authored `type`."""
    bundle = load_bundle(Path(path), exclude)
    by_type: dict[str, list[dict[str, object]]] = {}
    for row in bundle.concepts.execute().to_dict(orient="records"):
        concept_type = str(row.get("concept_type") or "concept")
        frontmatter_raw = row.get("frontmatter_json")
        if not isinstance(frontmatter_raw, str):
            message = f"concept {row.get('path')!r} has no serialized frontmatter"
            raise SchemaExportError(message)
        frontmatter = json.loads(frontmatter_raw)
        if not isinstance(frontmatter, dict):
            message = f"concept {row.get('path')!r} frontmatter is not an object"
            raise SchemaExportError(message)
        typed_frontmatter = cast("dict[str, object]", frontmatter)
        if concept_type == PROJECTION_TYPE:
            _restore_projection_booleans(typed_frontmatter)
        by_type.setdefault(concept_type, []).append(typed_frontmatter)
    return by_type


def _declared_types_by_type(
    root: str,
    concept_types: Sequence[str],
    spec_template: str | None,
) -> dict[str, dict[str, DuckDBLogicalType]]:
    """Read each type's declaration while retaining DuckDB's full catalog types."""
    if spec_template is None:
        return {}

    types_by_path: dict[str, list[str]] = {}
    for concept_type in concept_types:
        relative = declared_schema_relative_path(spec_template, concept_type)
        if relative is not None:
            types_by_path.setdefault(relative, []).append(concept_type)

    by_type: dict[str, dict[str, DuckDBLogicalType]] = {}
    for relative, owners in sorted(types_by_path.items()):
        schema_path = Path(root) / relative
        if not schema_path.is_file():
            continue
        if len(owners) > 1:
            names = ", ".join(repr(name) for name in sorted(owners))
            message = f"declared schema path collision at {relative!r}: {names}"
            raise SchemaExportError(message)

        concept_type = owners[0]
        try:
            sql_text = schema_path.read_text(encoding="utf-8")
            declared = parse_declared_schema(sql_text, concept_type)
        except (OSError, UnicodeError, DeclaredSchemaError) as exc:
            message = f"invalid declared schema for {concept_type!r} at {relative!r}: {exc}"
            raise SchemaExportError(message) from exc

        if declared.columns:
            by_type[concept_type] = dict(declared.columns)
    return by_type


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
    """Compile bundle observations into deterministic language-neutral contracts.

    `relational_schema` is the opt-in half: given the bundle's `okf.schema.sql`,
    every field participating in a declared foreign key compiles to a reference
    node. Projection documents compose a root contract with named sibling-schema
    references; they never become concept types themselves.
    """
    observed = {
        # A projection is a composed shape over the concept types, not one of
        # them: RFC 0018 section 5. Compiling it as observations would mint a
        # contract whose fields are `name`, `root` and `include`.
        concept_type: documents
        for concept_type, documents in documents_by_type(path, exclude).items()
        if concept_type != PROJECTION_TYPE
    }
    declared_by_type = _declared_types_by_type(path, tuple(observed), spec_template)
    concept_contracts = compile_contracts(
        observed,
        infer_types=infer_types,
        casts=casts,
        declared_types_by_type=declared_by_type,
    )
    projections = load_projections(path, exclude, relational_schema=relational_schema)
    if relational_schema is None:
        if refs == "embed":
            message = "--refs=embed needs a relational schema to know what to embed"
            raise SchemaExportError(message)
        return concept_contracts

    declared = Path(relational_schema)
    # `check --relational-schema` resolves a relative path against the bundle
    # root; the same flag has to mean the same file here.
    resolved = declared if declared.is_absolute() else Path(path) / declared
    schema = load_relational_schema(resolved)

    # A projection controls composition itself. Its root therefore keeps the
    # row-shaped/key form while only declared members become embedded sibling
    # references. Flat concept exports continue to honor the run-level refs mode.
    key_contracts = apply_references(concept_contracts, schema.foreign_keys, embed=False)
    flat_contracts = (
        key_contracts
        if refs == "key"
        else apply_references(concept_contracts, schema.foreign_keys, embed=True)
    )
    return (*flat_contracts, *compile_projections(projections, key_contracts))


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


def _has_embedded_reference(node: object) -> bool:
    """Whether a contract subtree needs the shared sibling-schema definition pool."""
    if isinstance(node, RefNode):
        return node.embedded
    if isinstance(node, ListNode):
        return _has_embedded_reference(node.item)
    if isinstance(node, ObjectNode):
        return any(_has_embedded_reference(field.value) for field in node.fields)
    return False


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
    contracts = build_schema_contracts(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
        relational_schema=relational_schema,
        refs=refs,
    )
    schemas = {contract.concept_type: contract_json_schema(contract) for contract in contracts}
    payload: dict[str, Any] = {
        "root": str(Path(path).resolve()),
        "total_types": len(schemas),
        "inferred_types": infer_types,
        "casts": list(casts),
        "schemas": schemas,
    }
    if any(_has_embedded_reference(contract.root) for contract in contracts):
        # Every embedded `$ref` points at `#/$defs/<type>`, so a consumer needs
        # the common pool those pointers resolve against. Projection members are
        # embedded even when flat concept export uses the default key mode.
        payload["defs"] = schemas
    return payload


def export_zod_schema(  # each argument is an independent public export flag.
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
    contracts = build_schema_contracts(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
        relational_schema=relational_schema,
        refs=refs,
    )
    return render_zod(contracts, zod_import=zod_import)


__all__ = [
    "SchemaCastError",
    "SchemaExportError",
    "SchemaNameCollisionError",
    "build_pydantic_models",
    "build_schema_contracts",
    "export_json_schema",
    "export_pydantic_source",
    "export_zod_schema",
]
