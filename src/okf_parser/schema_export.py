"""Export JSON Schema and Zod definitions for OKF concept frontmatter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import pandas as pd
from pydantic import BaseModel, create_model

from okf_parser.bundle import load_bundle

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

CastKind = Literal["string", "boolean", "integer", "number", "date", "datetime"]
type FieldDefinition = tuple[Any, Any]

_CAST_KINDS = frozenset({"string", "boolean", "integer", "number", "date", "datetime"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IDENTIFIER_RE = re.compile(r"[^0-9A-Za-z_]+")


class SchemaCastError(ValueError):
    """Raised when an explicit schema cast is invalid or cannot be applied."""


@dataclass(slots=True)
class _SchemaOptions:
    infer_types: bool
    casts: dict[str, CastKind]
    used_casts: set[str] = field(default_factory=set)


def _parse_casts(specifications: Sequence[str]) -> dict[str, CastKind]:
    casts: dict[str, CastKind] = {}
    for specification in specifications:
        path, separator, raw_kind = specification.rpartition("=")
        path = path.strip()
        kind = raw_kind.strip().lower()
        if not separator or not path or kind not in _CAST_KINDS:
            allowed = ", ".join(sorted(_CAST_KINDS))
            message = (
                f"invalid cast {specification!r}; expected FIELD=TYPE, where TYPE is {allowed}"
            )
            raise SchemaCastError(message)
        previous = casts.get(path)
        if previous is not None and previous != kind:
            message = f"field {path!r} has conflicting casts: {previous} and {kind}"
            raise SchemaCastError(message)
        casts[path] = cast("CastKind", kind)
    return casts


def _model_name(value: str, suffix: str) -> str:
    """Return a deterministic valid Python identifier for a generated model."""
    normalized = _IDENTIFIER_RE.sub("_", value).strip("_")
    parts = [part for part in normalized.split("_") if part]
    name = "".join(part[:1].upper() + part[1:] for part in parts) or "Concept"
    if name[0].isdigit():
        name = f"Concept{name}"
    return f"{name}{suffix}"


def _series(values: Sequence[str]) -> pd.Series:
    return pd.Series(values, dtype="string")


def _boolean_values(values: Sequence[str]) -> bool:
    return bool(values) and bool(_series(values).str.lower().isin(("true", "false")).all())


def _numeric_kind(values: Sequence[str]) -> CastKind | None:
    if not values:
        return None
    converted = pd.to_numeric(_series(values), errors="coerce")
    if converted.isna().any():
        return None
    return "integer" if converted.mod(1).eq(0).all() else "number"


def _temporal_kind(values: Sequence[str]) -> CastKind | None:
    if not values:
        return None
    converted = pd.to_datetime(_series(values), errors="coerce", format="mixed")
    if converted.isna().any():
        return None
    return "date" if all(_DATE_RE.fullmatch(value) for value in values) else "datetime"


def _can_cast(values: Sequence[str], kind: CastKind) -> bool:
    if not values or kind == "string":
        return True
    if kind == "boolean":
        return _boolean_values(values)
    if kind == "integer":
        return _numeric_kind(values) == "integer"
    if kind == "number":
        return _numeric_kind(values) in {"integer", "number"}
    if kind == "date":
        return _temporal_kind(values) == "date"
    return _temporal_kind(values) in {"date", "datetime"}


def _infer_kind(values: Sequence[str]) -> CastKind:
    """Use pandas conversions as best-effort heuristics, falling back to string."""
    if _boolean_values(values):
        return "boolean"
    numeric = _numeric_kind(values)
    if numeric is not None:
        return numeric
    temporal = _temporal_kind(values)
    return temporal or "string"


def _python_type(kind: CastKind) -> type:
    return {
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": float,
        "date": date,
        "datetime": datetime,
    }[kind]


def _scalar_annotation(values: Sequence[str], path: str, options: _SchemaOptions) -> type:
    explicit = options.casts.get(path)
    if explicit is not None:
        options.used_casts.add(path)
        if not _can_cast(values, explicit):
            sample = next(
                (value for value in values if not _can_cast((value,), explicit)),
                values[0],
            )
            message = f"cannot cast {path!r} to {explicit}: {sample!r} is incompatible"
            raise SchemaCastError(message)
        return _python_type(explicit)
    return _python_type(_infer_kind(values) if options.infer_types else "string")


def _field_annotation(
    values: Sequence[object],
    *,
    path: str,
    nested_name: str,
    options: _SchemaOptions,
) -> Any:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return str

    if all(isinstance(value, dict) for value in non_null):
        if path in options.casts:
            message = f"cannot cast {path!r}: the field contains objects"
            raise SchemaCastError(message)
        documents = [cast("dict[str, object]", value) for value in non_null]
        return _build_model(nested_name, documents, path=path, options=options)

    if all(isinstance(value, list) for value in non_null):
        items = [item for value in non_null for item in cast("list[object]", value)]
        if not items:
            item_annotation: Any = _scalar_annotation((), path, options)
        else:
            item_annotation = _field_annotation(
                items,
                path=path,
                nested_name=f"{nested_name}Item",
                options=options,
            )
        return list[item_annotation]

    if any(isinstance(value, (dict, list)) for value in non_null):
        if path in options.casts:
            message = f"cannot cast {path!r}: the field mixes scalar and structured values"
            raise SchemaCastError(message)
        return Any

    scalar_values = [str(value) for value in non_null]
    return _scalar_annotation(scalar_values, path, options)


def _build_model(
    name: str,
    documents: Sequence[Mapping[str, object]],
    *,
    path: str,
    options: _SchemaOptions,
    concept_type: str | None = None,
) -> type[BaseModel]:
    keys = {key for document in documents for key in document}
    if concept_type is not None:
        keys.update(("type", "title", "description"))

    fields: dict[str, FieldDefinition] = {}
    for key in sorted(keys):
        field_path = f"{path}.{key}" if path else key
        present_values = [document[key] for document in documents if key in document]
        required = len(present_values) == len(documents)
        nullable = any(value is None for value in present_values)

        if key == "type" and concept_type is not None:
            annotation: Any = Literal[concept_type]
            required = True
            nullable = False
        else:
            annotation = _field_annotation(
                present_values,
                path=field_path,
                nested_name=_model_name(f"{name}_{key}", "Structure"),
                options=options,
            )

        if not required or nullable:
            annotation |= None
            default: Any = None
        else:
            default = ...
        fields[key] = (annotation, default)

    return create_model(name, **fields)


def build_pydantic_models(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
) -> dict[str, type[BaseModel]]:
    """Build one dynamic model per concept type from every observed document.

    Scalars are strings unless ``infer_types`` is enabled or an explicit
    ``field=type`` cast is supplied. Inference is best effort and falls back to
    string; explicit casts are strict and report incompatible observed values.
    """
    bundle = load_bundle(Path(path), exclude)
    options = _SchemaOptions(infer_types=infer_types, casts=_parse_casts(casts))
    documents_by_type: dict[str, list[dict[str, object]]] = {}

    for row in bundle.concepts.execute().to_dict(orient="records"):
        concept_type = str(row.get("concept_type") or "concept")
        frontmatter_raw = row.get("frontmatter_json")
        if not isinstance(frontmatter_raw, str):
            message = f"concept {row.get('path')!r} has no serialized frontmatter"
            raise SchemaCastError(message)
        frontmatter = json.loads(frontmatter_raw)
        if not isinstance(frontmatter, dict):
            message = f"concept {row.get('path')!r} frontmatter is not an object"
            raise SchemaCastError(message)
        documents_by_type.setdefault(concept_type, []).append(frontmatter)

    models = {
        concept_type: _build_model(
            _model_name(concept_type, "Concept"),
            documents,
            path="",
            options=options,
            concept_type=concept_type,
        )
        for concept_type, documents in sorted(documents_by_type.items())
    }

    unused = set(options.casts) - options.used_casts
    if unused:
        fields = ", ".join(repr(item) for item in sorted(unused))
        raise SchemaCastError(f"cast field was not found in the bundle: {fields}")
    return models


def export_json_schema(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
) -> dict[str, Any]:
    """Export JSON Schema for each concept type."""
    models = build_pydantic_models(path, exclude, infer_types=infer_types, casts=casts)
    schemas = {concept_type: model.model_json_schema() for concept_type, model in models.items()}
    return {
        "root": str(Path(path).resolve()),
        "total_types": len(schemas),
        "inferred_types": infer_types,
        "casts": list(casts),
        "schemas": schemas,
    }


def _resolve_ref(schema: dict[str, Any], definitions: Mapping[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return schema
    prefix = "#/$defs/"
    if not reference.startswith(prefix):
        return schema
    resolved = definitions.get(reference.removeprefix(prefix))
    return resolved if isinstance(resolved, dict) else schema


def _schema_to_zod(schema: dict[str, Any], definitions: Mapping[str, Any]) -> str:
    schema = _resolve_ref(schema, definitions)
    if "const" in schema:
        return f"z.literal({json.dumps(schema['const'], ensure_ascii=False)})"

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        non_null = [
            item for item in any_of if isinstance(item, dict) and item.get("type") != "null"
        ]
        nullable = len(non_null) != len(any_of)
        if len(non_null) == 1:
            rendered = _schema_to_zod(non_null[0], definitions)
        else:
            rendered = (
                "z.union(["
                + ", ".join(_schema_to_zod(item, definitions) for item in non_null)
                + "])"
            )
        return f"{rendered}.nullable()" if nullable else rendered

    schema_type = schema.get("type")
    if schema_type == "boolean":
        return "z.boolean()"
    if schema_type == "integer":
        return "z.number().int()"
    if schema_type == "number":
        return "z.number()"
    if schema_type == "array":
        items = schema.get("items")
        item_schema = items if isinstance(items, dict) else {"type": "string"}
        return f"z.array({_schema_to_zod(item_schema, definitions)})"
    if schema_type == "object":
        properties = schema.get("properties")
        property_map = properties if isinstance(properties, dict) else {}
        required_value = schema.get("required")
        required = set(required_value) if isinstance(required_value, list) else set()
        rows = []
        for key, value in property_map.items():
            child = value if isinstance(value, dict) else {}
            rendered = _schema_to_zod(child, definitions)
            if key not in required:
                rendered += ".optional()"
            rows.append(f"  {json.dumps(key, ensure_ascii=False)}: {rendered}")
        return "z.object({\n" + ",\n".join(rows) + "\n})"
    if schema_type == "string" and schema.get("format") == "date":
        return "z.string().date()"
    if schema_type == "string" and schema.get("format") == "date-time":
        return "z.string().datetime({ offset: true, local: true })"
    return "z.string()"


def export_zod_schema(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
) -> str:
    """Generate Zod definitions for Astro Content Collections."""
    report = export_json_schema(path, exclude, infer_types=infer_types, casts=casts)
    schemas = report["schemas"]
    lines = ["// Generated by okf-parser", "import { z } from 'astro:content';", ""]
    for concept_type, value in cast("dict[str, dict[str, Any]]", schemas).items():
        definitions = value.get("$defs")
        definition_map = definitions if isinstance(definitions, dict) else {}
        variable = _model_name(concept_type, "Schema")
        lines.append(f"export const {variable} = {_schema_to_zod(value, definition_map)};")
        lines.append("")
    return "\n".join(lines)
