"""Export canonical JSON Schema, Zod, and Python models for OKF frontmatter."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, create_model

from okf_parser.bundle import load_bundle
from okf_parser.declared_schema import (
    DeclaredSchemaError,
    declared_schema_relative_path,
    parse_declared_schema,
)
from okf_parser.schema_contract import (
    AnyNode,
    ContractNode,
    ListNode,
    LiteralNode,
    ObjectNode,
    ScalarNode,
    SchemaCastError,
    SchemaExportError,
    SchemaNameCollisionError,
    TypeContract,
    ZodImport,
    compile_contracts,
    contract_json_schema,
    model_name,
    render_zod,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from okf_parser.duckdb_types import DuckDBLogicalType
    from okf_parser.schema_lexemes import CastKind

type FieldDefinition = tuple[Any, Any]


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
        by_type.setdefault(concept_type, []).append(cast("dict[str, object]", frontmatter))
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
) -> tuple[TypeContract, ...]:
    """Compile bundle observations into deterministic language-neutral contracts."""
    observed = documents_by_type(path, exclude)
    declared_by_type = _declared_types_by_type(path, tuple(observed), spec_template)
    return compile_contracts(
        observed,
        infer_types=infer_types,
        casts=casts,
        declared_types_by_type=declared_by_type,
    )


def _python_type(kind: CastKind) -> type:
    return {
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": float,
        "date": date,
        "datetime": datetime,
    }[kind]


def _annotation(node: ContractNode, *, name: str) -> object:
    if isinstance(node, ScalarNode):
        declared = node.declared_type
        if declared is None:
            return _python_type(node.kind)
        return {
            "string": str,
            "boolean": bool,
            "integer": int,
            "float": float,
            "decimal": Decimal,
            "date": date,
            "timestamp": datetime,
            "timestamptz": datetime,
            "uuid": UUID,
            "unsupported": Any,
        }.get(declared.family, Any)
    if isinstance(node, LiteralNode):
        return cast("Any", Literal)[node.value]
    if isinstance(node, AnyNode):
        return Any
    if isinstance(node, ListNode):
        item: Any = _annotation(node.item, name=f"{name}Item")
        if node.item_nullable:
            item = item | None
        return list[item]
    return _pydantic_model(name, node)


def _pydantic_model(name: str, node: ObjectNode) -> type[BaseModel]:
    fields: dict[str, FieldDefinition] = {}
    for field_contract in node.fields:
        nested_name = model_name(f"{name}_{field_contract.name}", "Structure")
        annotation: Any = _annotation(field_contract.value, name=nested_name)
        if field_contract.nullable:
            annotation = annotation | None
        default: Any = ... if field_contract.required else None
        fields[field_contract.name] = (annotation, default)
    return create_model(name, **cast("dict[str, Any]", fields))


def build_pydantic_models(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, type[BaseModel]]:
    """Build dynamic Pydantic adapters from the same contract used by exporters."""
    contracts = build_schema_contracts(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
    )
    return {
        contract.concept_type: _pydantic_model(contract.model_name, contract.root)
        for contract in contracts
    }


def export_json_schema(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, Any]:
    """Export the canonical JSON Schema representation for each concept type."""
    contracts = build_schema_contracts(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
    )
    schemas = {contract.concept_type: contract_json_schema(contract) for contract in contracts}
    return {
        "root": str(Path(path).resolve()),
        "total_types": len(schemas),
        "inferred_types": infer_types,
        "casts": list(casts),
        "schemas": schemas,
    }


def export_zod_schema(  # each argument is an independent public export flag.
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    zod_import: ZodImport = "zod",
    spec_template: str | None = None,
) -> str:
    """Generate canonical Zod declarations, using generic Zod by default."""
    contracts = build_schema_contracts(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
    )
    return render_zod(contracts, zod_import=zod_import)


__all__ = [
    "SchemaCastError",
    "SchemaExportError",
    "SchemaNameCollisionError",
    "build_pydantic_models",
    "build_schema_contracts",
    "export_json_schema",
    "export_zod_schema",
]
