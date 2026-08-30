"""Language-neutral schema contract shared by Python exporters and conformance tests."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from okf_parser.schema_lexemes import CastKind, can_classify_as, classify_lexemes

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from okf_parser.duckdb_types import DuckDBLogicalType

type ZodImport = Literal["zod", "astro"]
type ContractNode = ScalarNode | LiteralNode | ObjectNode | ListNode | AnyNode | RefNode

_CAST_KINDS = frozenset({"string", "boolean", "integer", "number", "date", "datetime"})


class SchemaExportError(ValueError):
    """Base error for schema generation failures."""


class SchemaCastError(SchemaExportError):
    """Raised when an explicit schema cast is invalid or cannot be applied."""


class SchemaNameCollisionError(SchemaExportError):
    """Raised when distinct concept types normalize to the same generated name."""


@dataclass(frozen=True, slots=True)
class ScalarNode:
    """One scalar value, optionally retaining its exact declared DuckDB type."""

    kind: CastKind
    declared_type: DuckDBLogicalType | None = None


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
    declared_type: DuckDBLogicalType | None = None


@dataclass(frozen=True, slots=True)
class RefNode:
    """A field whose value identifies a document of another concept type.

    The node wraps, rather than replaces, the scalar the field carries: under
    ``--refs=key`` a reference is a type-level fact about the value, not a
    promise that the consumer holds the referenced document.
    """

    concept_type: str
    columns: tuple[str, ...]
    referenced_columns: tuple[str, ...]
    position: int
    value: ContractNode
    embedded: bool = False

    @property
    def reference_metadata(self) -> dict[str, object]:
        """Return the deterministic payload every format publishes."""
        return {
            "type": self.concept_type,
            "columns": list(self.columns),
            "referencedColumns": list(self.referenced_columns),
            "position": self.position,
        }

    @property
    def description(self) -> str:
        """Return the one-line description formats without metadata can carry."""
        targets = ", ".join(self.referenced_columns)
        return f"references {self.concept_type}({targets})"


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
    declared_by_type: Mapping[str, Mapping[str, DuckDBLogicalType]] = field(default_factory=dict)
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
    values: Sequence[str], path: str, options: _CompileOptions, _concept_type: str | None
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
    kind = classify_lexemes(values) if options.infer_types else "string"
    return ScalarNode(kind)


def _declared_node(declared: DuckDBLogicalType) -> ContractNode:
    """Project a lossless DuckDB type into the language-neutral contract tree."""
    if declared.family == "list" and declared.element is not None:
        return ListNode(
            item=_declared_node(declared.element),
            item_nullable=False,
            declared_type=declared,
        )
    return ScalarNode(declared.cast_kind or "string", declared_type=declared)


def _compile_value(
    values: Sequence[object],
    *,
    path: str,
    options: _CompileOptions,
    concept_type: str | None,
) -> ContractNode:
    non_null = [value for value in values if value is not None]
    declared = (
        options.declared_by_type.get(concept_type, {}).get(path)
        if concept_type is not None and path not in options.casts
        else None
    )
    if declared is not None:
        node = _declared_node(declared)
        if isinstance(node, ListNode):
            items = [
                item
                for value in non_null
                if isinstance(value, list)
                for item in cast("list[object]", value)
            ]
            node = ListNode(
                node.item,
                item_nullable=any(item is None for item in items),
                declared_type=node.declared_type,
            )
        return node
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
    declared_types_by_type: Mapping[str, Mapping[str, DuckDBLogicalType]] | None = None,
) -> tuple[TypeContract, ...]:
    """Compile all observations into one language-neutral contract per type.

    `casts` (explicit `--cast FIELD=TYPE`) stays a single global table, same
    as always - a caller asking for a cast by field path alone accepts that
    it applies wherever that path appears. `declared_types_by_type` is the
    opposite shape on purpose: each type's `.schema.sql` only ever describes
    that type, so a declared kind is looked up scoped to `(concept_type,
    field)` and never bleeds into another type's same-named field. Where
    both apply to one type's field, the explicit cast wins (`_scalar_node`
    checks it first) - precedence still resolved per type, not globally.
    """
    options = _CompileOptions(
        infer_types=infer_types,
        casts=parse_casts(casts),
        declared_by_type=declared_types_by_type or {},
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


def _declared_scalar_schema(declared: DuckDBLogicalType) -> dict[str, object]:
    """Render one declared physical type without discarding DuckDB intent."""
    schema: dict[str, object] = {"x-okf-duckdb-type": declared.sql}
    if declared.family == "string":
        schema["type"] = "string"
    elif declared.family == "boolean":
        schema["type"] = "boolean"
    elif declared.family == "integer":
        schema["type"] = "integer"
    elif declared.family in {"float", "decimal"}:
        schema["type"] = "number"
        if declared.family == "decimal" and declared.scale:
            schema["multipleOf"] = 10.0**-declared.scale
    elif declared.family == "date":
        schema.update({"type": "string", "format": "date"})
    elif declared.family == "timestamp":
        schema.update({"type": "string", "x-okf-temporal-kind": "timestamp-without-time-zone"})
    elif declared.family == "timestamptz":
        schema.update({"type": "string", "format": "date-time"})
    elif declared.family == "uuid":
        schema.update({"type": "string", "format": "uuid"})
    return schema


def _scalar_schema(node: ScalarNode) -> dict[str, object]:
    if node.declared_type is not None:
        return _declared_scalar_schema(node.declared_type)
    schemas: dict[CastKind, dict[str, object]] = {
        "boolean": {"type": "boolean"},
        "integer": {"type": "integer"},
        "number": {"type": "number"},
        "date": {"type": "string", "format": "date"},
        "datetime": {"type": "string", "format": "date-time"},
        "string": {"type": "string"},
    }
    return schemas[node.kind]


def _reference_json_schema(node: RefNode) -> dict[str, object]:
    """Render a reference: a `$ref` when embedded, the carried scalar otherwise."""
    if node.embedded:
        return {
            "$ref": f"#/$defs/{node.concept_type}",
            "x-okf-references": node.reference_metadata,
        }
    return {
        **node_json_schema(node.value),
        "x-okf-references": node.reference_metadata,
    }


def node_json_schema(node: ContractNode) -> dict[str, object]:
    """Render one canonical JSON Schema fragment from the shared contract."""
    if isinstance(node, RefNode):
        return _reference_json_schema(node)
    if isinstance(node, ScalarNode):
        return _scalar_schema(node)
    if isinstance(node, LiteralNode):
        return {"type": "string", "const": node.value}
    if isinstance(node, AnyNode):
        return {}
    if isinstance(node, ListNode):
        item = node_json_schema(node.item)
        if node.item_nullable:
            item = {"anyOf": [item, {"type": "null"}]}
        schema: dict[str, object] = {"type": "array", "items": item}
        if node.declared_type is not None:
            schema["x-okf-duckdb-type"] = node.declared_type.sql
        return schema

    return _object_json_schema(node)


def _object_json_schema(node: ObjectNode) -> dict[str, object]:
    """Render one object node, keeping field order and required-ness authored."""
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


def _declared_scalar_zod(declared: DuckDBLogicalType) -> str:
    if declared.family == "integer":
        return (
            "z.number().int()"
            if declared.is_js_safe_integer
            else "z.union([z.number().int(), z.bigint()])"
        )
    renderers: dict[str, str] = {
        "string": "z.string()",
        "boolean": "z.boolean()",
        "float": "z.number()",
        "decimal": "z.string()",
        "date": "z.iso.date()",
        "timestamp": "z.string()",
        "timestamptz": "z.iso.datetime({ offset: true })",
        "uuid": "z.uuid()",
    }
    return renderers.get(declared.family, "z.unknown()")


def _scalar_zod(node: ScalarNode) -> str:
    if node.declared_type is not None:
        return _declared_scalar_zod(node.declared_type)
    renderers: dict[CastKind, str] = {
        "boolean": "z.boolean()",
        "integer": "z.number().int()",
        "number": "z.number()",
        "date": "z.iso.date()",
        "datetime": "z.iso.datetime({ offset: true, local: true })",
        "string": "z.string()",
    }
    return renderers[node.kind]


def _reference_zod(
    node: RefNode,
    indent: str,
    *,
    ref_names: Mapping[str, str] | None,
    lazy_refs: frozenset[str],
) -> str:
    """Render a reference: the sibling variable when embedded, the scalar otherwise."""
    if node.embedded:
        name = _ref_variable(node.concept_type, ref_names)
        return f"z.lazy(() => {name})" if node.concept_type in lazy_refs else name
    described = json.dumps(node.description, ensure_ascii=False)
    rendered = node_zod(node.value, indent, ref_names=ref_names, lazy_refs=lazy_refs)
    return f"{rendered}.describe({described})"


def node_zod(
    node: ContractNode,
    indent: str = "",
    *,
    ref_names: Mapping[str, str] | None = None,
    lazy_refs: frozenset[str] = frozenset(),
) -> str:
    """Render one canonical Zod expression from the shared contract.

    `ref_names` maps a concept type to the variable its schema is declared
    under; `lazy_refs` names the targets whose declaration cannot precede this
    one, which are emitted through `z.lazy` so a cycle closes by name.
    """
    if isinstance(node, RefNode):
        return _reference_zod(node, indent, ref_names=ref_names, lazy_refs=lazy_refs)
    if isinstance(node, ScalarNode):
        return _scalar_zod(node)
    if isinstance(node, LiteralNode):
        return f"z.literal({json.dumps(node.value, ensure_ascii=False)})"
    if isinstance(node, AnyNode):
        return "z.unknown()"
    if isinstance(node, ListNode):
        item = node_zod(node.item, indent, ref_names=ref_names, lazy_refs=lazy_refs)
        if node.item_nullable:
            item += ".nullable()"
        return f"z.array({item})"

    child_indent = f"{indent}  "
    rows: list[str] = []
    for field_contract in node.fields:
        rendered = node_zod(
            field_contract.value, child_indent, ref_names=ref_names, lazy_refs=lazy_refs
        )
        if field_contract.nullable:
            rendered += ".nullable()"
        if not field_contract.required:
            rendered += ".optional()"
        rows.append(
            f"{child_indent}{json.dumps(field_contract.name, ensure_ascii=False)}: {rendered}"
        )
    return f"z.object({{\n{',\n'.join(rows)}\n{indent}}})"


def _ref_variable(concept_type: str, ref_names: Mapping[str, str] | None) -> str:
    """Return the variable a referenced concept type is declared under."""
    if ref_names is not None and concept_type in ref_names:
        return ref_names[concept_type]
    return model_name(concept_type, "Schema")


def _embedded_targets(contract: TypeContract) -> tuple[str, ...]:
    """Return the concept types this contract embeds, in authored field order."""
    return tuple(
        field_contract.value.concept_type
        for field_contract in contract.root.fields
        if isinstance(field_contract.value, RefNode) and field_contract.value.embedded
    )


def _declaration_order(
    contracts: Sequence[TypeContract],
) -> tuple[tuple[TypeContract, ...], frozenset[str]]:
    """Order declarations so a target precedes its user, naming the back edges.

    A JavaScript `const` cannot be read before it is declared, so an embedded
    reference needs its target declared first. A cycle makes that impossible
    for at least one edge; those edges are returned so the renderer can close
    them with `z.lazy`, which defers the read to call time.
    """
    by_type = {contract.concept_type: contract for contract in contracts}
    ordered: list[TypeContract] = []
    emitted: set[str] = set()
    visiting: list[str] = []
    lazy: set[str] = set()

    def visit(concept_type: str) -> None:
        if concept_type in emitted or concept_type not in by_type:
            return
        if concept_type in visiting:
            lazy.add(concept_type)
            return
        visiting.append(concept_type)
        for target in _embedded_targets(by_type[concept_type]):
            visit(target)
        visiting.pop()
        emitted.add(concept_type)
        ordered.append(by_type[concept_type])

    for contract in contracts:
        visit(contract.concept_type)
    return tuple(ordered), frozenset(lazy)


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
    ordered, lazy_refs = _declaration_order(contracts)
    for contract in ordered:
        rendered = node_zod(contract.root, ref_names=variable_names, lazy_refs=lazy_refs)
        lines.append(f"export const {variable_names[contract.concept_type]} = {rendered};")
        lines.append("")
    return "\n".join(lines)
