"""Language-neutral schema contract shared by Python exporters and conformance tests."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from okf_parser.schema_lexemes import CastKind, can_classify_as, classify_lexemes

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

type ZodImport = Literal["zod", "astro"]
type ContractNode = ScalarNode | LiteralNode | ObjectNode | ListNode | AnyNode

_CAST_KINDS = frozenset({"string", "boolean", "integer", "number", "date", "datetime"})


class SchemaExportError(ValueError):
    """Base error for schema generation failures."""


class SchemaCastError(SchemaExportError):
    """Raised when an explicit schema cast is invalid or cannot be applied."""


class SchemaNameCollisionError(SchemaExportError):
    """Raised when distinct concept types normalize to the same generated name."""


@dataclass(frozen=True, slots=True)
class ScalarNode:
    """One scalar value classified by its exact lexical kind."""

    kind: CastKind


@dataclass(frozen=True, slots=True)
class LiteralNode:
    """One required literal value, currently used for concept ``type``."""

    value: str


@dataclass(frozen=True, slots=True)
class AnyNode:
    """A field whose observations mix incompatible structural categories."""


@dataclass(frozen=True, slots=True)
class ListNode:
    """A homogeneous list whose items may independently admit null."""

    item: ContractNode
    item_nullable: bool


@dataclass(frozen=True, slots=True)
class FieldContract:
    """One object field with independent presence and nullability semantics."""

    name: str
    required: bool
    nullable: bool
    value: ContractNode


@dataclass(frozen=True, slots=True)
class ObjectNode:
    """An object with deterministically ordered fields."""

    fields: tuple[FieldContract, ...]


@dataclass(frozen=True, slots=True)
class TypeContract:
    """One concept type and its generated model name."""

    concept_type: str
    model_name: str
    root: ObjectNode


@dataclass(slots=True)
class _CompileOptions:
    infer_types: bool
    casts: dict[str, CastKind]
    declared_by_type: Mapping[str, Mapping[str, CastKind]] = field(default_factory=dict)
    used_casts: set[str] = field(default_factory=set)


def parse_casts(specifications: Sequence[str]) -> dict[str, CastKind]:
    """Parse repeatable ``FIELD=TYPE`` declarations and reject ambiguity."""
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


def model_name(value: str, suffix: str) -> str:
    """Return the shared deterministic Unicode-aware generated identifier."""
    normalized = unicodedata.normalize("NFKC", value)
    identifier = "".join(
        character if character == "_" or character.isalnum() else "_" for character in normalized
    ).strip("_")
    parts = [part for part in identifier.split("_") if part]
    name = "".join(part[:1].upper() + part[1:] for part in parts) or "Concept"
    if name[0].isdigit():
        name = f"Concept{name}"
    if not name.isidentifier():
        encoded = "".join(f"U{ord(character):04X}" for character in normalized)
        name = f"Concept{encoded}" if encoded else "Concept"
    return f"{name}{suffix}"


def unique_model_names(values: Sequence[str], suffix: str) -> dict[str, str]:
    """Generate names while rejecting collisions instead of silently overwriting."""
    names: dict[str, str] = {}
    owners: dict[str, str] = {}
    for value in sorted(values):
        name = model_name(value, suffix)
        previous = owners.get(name)
        if previous is not None and previous != value:
            message = f"concept types {previous!r} and {value!r} both normalize to {name!r}"
            raise SchemaNameCollisionError(message)
        names[value] = name
        owners[name] = value
    return names


def _scalar_node(
    values: Sequence[str], path: str, options: _CompileOptions, concept_type: str | None
) -> ScalarNode:
    explicit = options.casts.get(path)
    if explicit is not None:
        options.used_casts.add(path)
        if not can_classify_as(values, explicit):
            sample = next(
                (value for value in values if not can_classify_as((value,), explicit)),
                values[0],
            )
            message = f"cannot cast {path!r} to {explicit}: {sample!r} is incompatible"
            raise SchemaCastError(message)
        return ScalarNode(explicit)
    # Declared casts are looked up scoped to `concept_type`, never as a flat
    # path->kind table: a `.schema.sql` declaring `Fatura.valor` as a
    # DECIMAL must never leak into `Pesquisa.valor` just because the two
    # types happen to share a field name. Unlike an explicit `--cast`,
    # advisory only - a value that doesn't fit falls through to inference
    # or `"string"`, never raises.
    if concept_type is not None:
        declared = options.declared_by_type.get(concept_type, {}).get(path)
        if declared is not None and can_classify_as(values, declared):
            return ScalarNode(declared)
    kind = classify_lexemes(values) if options.infer_types else "string"
    return ScalarNode(kind)


def _compile_value(
    values: Sequence[object],
    *,
    path: str,
    options: _CompileOptions,
    concept_type: str | None,
) -> ContractNode:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return _scalar_node((), path, options, concept_type)

    if all(isinstance(value, dict) for value in non_null):
        if path in options.casts:
            message = f"cannot cast {path!r}: the field contains objects"
            raise SchemaCastError(message)
        documents = [cast("dict[str, object]", value) for value in non_null]
        return _compile_object(documents, parent_path=path, options=options)

    if all(isinstance(value, list) for value in non_null):
        items = [item for value in non_null for item in cast("list[object]", value)]
        return ListNode(
            item=_compile_value(items, path=path, options=options, concept_type=None),
            item_nullable=any(item is None for item in items),
        )

    if any(isinstance(value, (dict, list)) for value in non_null):
        if path in options.casts:
            message = f"cannot cast {path!r}: the field mixes scalar and structured values"
            raise SchemaCastError(message)
        return AnyNode()

    return _scalar_node([str(value) for value in non_null], path, options, concept_type)


def _compile_object(
    documents: Sequence[Mapping[str, object]],
    *,
    parent_path: str,
    options: _CompileOptions,
    concept_type: str | None = None,
) -> ObjectNode:
    keys = {key for document in documents for key in document}
    if concept_type is not None:
        keys.update(("type", "title", "description"))
        if not parent_path:
            keys.update(options.declared_by_type.get(concept_type, {}))

    fields: list[FieldContract] = []
    for name in sorted(keys):
        field_path = f"{parent_path}.{name}" if parent_path else name
        present = [document[name] for document in documents if name in document]
        required = len(present) == len(documents)
        nullable = any(value is None for value in present)
        if name == "type" and concept_type is not None:
            required = True
            nullable = False
            value: ContractNode = LiteralNode(concept_type)
        else:
            value = _compile_value(
                present, path=field_path, options=options, concept_type=concept_type
            )
        fields.append(FieldContract(name, required, nullable, value))
    return ObjectNode(tuple(fields))


def compile_contracts(
    documents_by_type: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    declared_casts_by_type: Mapping[str, Mapping[str, CastKind]] | None = None,
) -> tuple[TypeContract, ...]:
    """Compile all observations into one language-neutral contract per type.

    `casts` (explicit `--cast FIELD=TYPE`) stays a single global table, same
    as always - a caller asking for a cast by field path alone accepts that
    it applies wherever that path appears. `declared_casts_by_type` is the
    opposite shape on purpose: each type's `.schema.sql` only ever describes
    that type, so a declared kind is looked up scoped to `(concept_type,
    field)` and never bleeds into another type's same-named field. Where
    both apply to one type's field, the explicit cast wins (`_scalar_node`
    checks it first) - precedence still resolved per type, not globally.
    """
    options = _CompileOptions(
        infer_types=infer_types,
        casts=parse_casts(casts),
        declared_by_type=declared_casts_by_type or {},
    )
    names = unique_model_names(tuple(documents_by_type), "Concept")
    contracts = tuple(
        TypeContract(
            concept_type=concept_type,
            model_name=names[concept_type],
            root=_compile_object(
                documents,
                parent_path="",
                options=options,
                concept_type=concept_type,
            ),
        )
        for concept_type, documents in sorted(documents_by_type.items())
    )
    unused = set(options.casts) - options.used_casts
    if unused:
        fields = ", ".join(repr(item) for item in sorted(unused))
        message = f"cast field was not found in the bundle: {fields}"
        raise SchemaCastError(message)
    return contracts


def _title_for(name: str) -> str:
    return name[:1].upper() + name[1:]


def _scalar_schema(kind: CastKind) -> dict[str, object]:
    if kind == "boolean":
        return {"type": "boolean"}
    if kind == "integer":
        return {"type": "integer"}
    if kind == "number":
        return {"type": "number"}
    if kind == "date":
        return {"type": "string", "format": "date"}
    if kind == "datetime":
        return {"type": "string", "format": "date-time"}
    return {"type": "string"}


def node_json_schema(node: ContractNode) -> dict[str, object]:
    """Render one canonical JSON Schema fragment from the shared contract."""
    if isinstance(node, ScalarNode):
        return _scalar_schema(node.kind)
    if isinstance(node, LiteralNode):
        return {"type": "string", "const": node.value}
    if isinstance(node, AnyNode):
        return {}
    if isinstance(node, ListNode):
        item = node_json_schema(node.item)
        if node.item_nullable:
            item = {"anyOf": [item, {"type": "null"}]}
        return {"type": "array", "items": item}

    properties: dict[str, object] = {}
    required: list[str] = []
    for field_contract in node.fields:
        base = node_json_schema(field_contract.value)
        property_schema: dict[str, object] = (
            {"anyOf": [base, {"type": "null"}]} if field_contract.nullable else dict(base)
        )
        property_schema["title"] = _title_for(field_contract.name)
        properties[field_contract.name] = property_schema
        if field_contract.required:
            required.append(field_contract.name)
    schema: dict[str, object] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def contract_json_schema(contract: TypeContract) -> dict[str, object]:
    """Render one complete canonical concept schema."""
    return {**node_json_schema(contract.root), "title": contract.model_name}


def _scalar_zod(kind: CastKind) -> str:
    if kind == "boolean":
        return "z.boolean()"
    if kind == "integer":
        return "z.number().int()"
    if kind == "number":
        return "z.number()"
    if kind == "date":
        return "z.iso.date()"
    if kind == "datetime":
        return "z.iso.datetime({ offset: true, local: true })"
    return "z.string()"


def node_zod(node: ContractNode, indent: str = "") -> str:
    """Render one canonical Zod expression from the shared contract."""
    if isinstance(node, ScalarNode):
        return _scalar_zod(node.kind)
    if isinstance(node, LiteralNode):
        return f"z.literal({json.dumps(node.value, ensure_ascii=False)})"
    if isinstance(node, AnyNode):
        return "z.unknown()"
    if isinstance(node, ListNode):
        item = node_zod(node.item, indent)
        if node.item_nullable:
            item += ".nullable()"
        return f"z.array({item})"

    child_indent = f"{indent}  "
    rows: list[str] = []
    for field_contract in node.fields:
        rendered = node_zod(field_contract.value, child_indent)
        if field_contract.nullable:
            rendered += ".nullable()"
        if not field_contract.required:
            rendered += ".optional()"
        rows.append(
            f"{child_indent}{json.dumps(field_contract.name, ensure_ascii=False)}: {rendered}"
        )
    return f"z.object({{\n{',\n'.join(rows)}\n{indent}}})"


def render_zod(contracts: Sequence[TypeContract], *, zod_import: ZodImport = "zod") -> str:
    """Render complete deterministic Zod declarations for all concept types."""
    variable_names = unique_model_names(
        tuple(contract.concept_type for contract in contracts),
        "Schema",
    )
    import_line = (
        "import { z } from 'astro:content';"
        if zod_import == "astro"
        else "import { z } from 'zod';"
    )
    lines = ["// Generated by okf-parser", import_line, ""]
    for contract in contracts:
        lines.append(
            f"export const {variable_names[contract.concept_type]} = {node_zod(contract.root)};"
        )
        lines.append("")
    return "\n".join(lines)
