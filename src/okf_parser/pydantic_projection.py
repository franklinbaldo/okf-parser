"""Project shared OKF schema contracts into Pydantic runtime models and source."""

from __future__ import annotations

import json
import keyword
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, create_model

from okf_parser.schema_contract import (
    AnyNode,
    ContractNode,
    ListNode,
    LiteralNode,
    ObjectNode,
    ScalarNode,
    SchemaNameCollisionError,
    TypeContract,
    model_name,
)

if TYPE_CHECKING:
    from okf_parser.schema_lexemes import CastKind

type FieldDefinition = tuple[Any, Any]
type StructuralPath = tuple[str, ...]

_PROTECTED_PYDANTIC_NAMES = frozenset(dir(BaseModel)) | {"model_config"}


@dataclass(frozen=True, slots=True)
class PydanticFieldName:
    """One safe Python attribute and its authored alias, when one is required."""

    python_name: str
    alias: str | None


@dataclass(slots=True)
class PydanticModelNameRegistry:
    """Reject module-global model-name collisions with structural provenance."""

    owners: dict[str, StructuralPath] = field(default_factory=dict)

    def register(self, name: str, path: StructuralPath) -> None:
        """Claim one emitted model name for exactly one structural path."""
        previous = self.owners.get(name)
        if previous is not None and previous != path:
            message = (
                f"Pydantic model name {name!r} collides between structural paths "
                f"{_path_label(previous)} and {_path_label(path)}"
            )
            raise SchemaNameCollisionError(message)
        self.owners[name] = path


def _path_label(path: StructuralPath) -> str:
    return " -> ".join(repr(segment) for segment in path)


def _normalized_identifier(authored: str) -> str:
    normalized = unicodedata.normalize("NFKC", authored)
    candidate = "".join(
        character if character == "_" or character.isalnum() else "_"
        for character in normalized
    ).strip("_")
    if not candidate:
        candidate = "field"
    if candidate[0].isdigit():
        candidate = f"field_{candidate}"
    if not candidate.isidentifier():
        encoded = "".join(f"u{ord(character):04x}" for character in normalized)
        candidate = f"field_{encoded}" if encoded else "field"
    return candidate


def pydantic_field_name(
    authored: str,
    used: dict[str, str],
) -> PydanticFieldName:
    """Map an authored YAML key to a safe Pydantic attribute without configuration."""
    normalized = unicodedata.normalize("NFKC", authored)
    direct = (
        authored == normalized
        and authored.isidentifier()
        and not keyword.iskeyword(authored)
        and not authored.startswith("_")
        and not authored.startswith("model_")
        and authored not in _PROTECTED_PYDANTIC_NAMES
    )
    if direct:
        candidate = authored
        alias = None
    else:
        candidate = _normalized_identifier(authored)
        if candidate.startswith("model_"):
            candidate = f"field_{candidate}"
        elif (
            authored.startswith("_")
            or keyword.iskeyword(candidate)
            or candidate in _PROTECTED_PYDANTIC_NAMES
        ):
            candidate = f"{candidate.rstrip('_')}_"
        if candidate.startswith("_"):
            candidate = f"field{candidate}"
        alias = authored

    previous = used.get(candidate)
    if previous is not None and previous != authored:
        message = (
            f"authored fields {previous!r} and {authored!r} both map to "
            f"Pydantic attribute {candidate!r}"
        )
        raise SchemaNameCollisionError(message)
    used[candidate] = authored
    return PydanticFieldName(candidate, alias)


def _python_type(kind: CastKind) -> type:
    return {
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": float,
        "date": date,
        "datetime": datetime,
    }[kind]


def _runtime_annotation(
    node: ContractNode,
    *,
    suggested_name: str,
    path: StructuralPath,
    registry: PydanticModelNameRegistry,
) -> object:
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
        item: Any = _runtime_annotation(
            node.item,
            suggested_name=f"{suggested_name}Item",
            path=(*path, "[]"),
            registry=registry,
        )
        if node.item_nullable:
            item = item | None
        return list[item]
    return _dynamic_model(suggested_name, node, path=path, registry=registry)


def _dynamic_model(
    name: str,
    node: ObjectNode,
    *,
    path: StructuralPath,
    registry: PydanticModelNameRegistry,
) -> type[BaseModel]:
    registry.register(name, path)
    fields: dict[str, FieldDefinition] = {}
    used: dict[str, str] = {}
    for field_contract in node.fields:
        mapped = pydantic_field_name(field_contract.name, used)
        nested_name = model_name(f"{name}_{field_contract.name}", "Structure")
        annotation: Any = _runtime_annotation(
            field_contract.value,
            suggested_name=nested_name,
            path=(*path, field_contract.name),
            registry=registry,
        )
        if field_contract.nullable:
            annotation = annotation | None
        default: Any = ... if field_contract.required else None
        if mapped.alias is not None:
            default = Field(default=default, alias=mapped.alias)
        fields[mapped.python_name] = (annotation, default)
    return create_model(
        name,
        __config__=ConfigDict(extra="allow"),
        **cast("dict[str, Any]", fields),
    )


def build_dynamic_pydantic_models(
    contracts: tuple[TypeContract, ...],
) -> dict[str, type[BaseModel]]:
    """Build dynamic Pydantic models under the same naming policy as source output."""
    registry = PydanticModelNameRegistry()
    models: dict[str, type[BaseModel]] = {}
    for contract in contracts:
        models[contract.concept_type] = _dynamic_model(
            contract.model_name,
            contract.root,
            path=(contract.concept_type,),
            registry=registry,
        )
    return models


@dataclass(slots=True)
class _SourceImports:
    datetime_names: set[str] = field(default_factory=set)
    typing_names: set[str] = field(default_factory=set)
    decimal: bool = False
    uuid: bool = False
    field: bool = False


@dataclass(frozen=True, slots=True)
class _SourceField:
    name: str
    annotation: str
    required: bool
    nullable: bool
    alias: str | None


@dataclass(frozen=True, slots=True)
class _SourceModel:
    name: str
    fields: tuple[_SourceField, ...]


@dataclass(slots=True)
class _SourceContext:
    registry: PydanticModelNameRegistry = field(default_factory=PydanticModelNameRegistry)
    imports: _SourceImports = field(default_factory=_SourceImports)
    models: list[_SourceModel] = field(default_factory=list)


def _source_scalar(node: ScalarNode, imports: _SourceImports) -> str:
    declared = node.declared_type
    if declared is None:
        annotation = {
            "string": "str",
            "boolean": "bool",
            "integer": "int",
            "number": "float",
            "date": "date",
            "datetime": "datetime",
        }[node.kind]
    else:
        annotation = {
            "string": "str",
            "boolean": "bool",
            "integer": "int",
            "float": "float",
            "decimal": "Decimal",
            "date": "date",
            "timestamp": "datetime",
            "timestamptz": "datetime",
            "uuid": "UUID",
            "unsupported": "Any",
        }.get(declared.family, "Any")

    if annotation in {"date", "datetime"}:
        imports.datetime_names.add(annotation)
    elif annotation == "Decimal":
        imports.decimal = True
    elif annotation == "UUID":
        imports.uuid = True
    elif annotation == "Any":
        imports.typing_names.add("Any")
    return annotation


def _source_annotation(
    node: ContractNode,
    *,
    suggested_name: str,
    path: StructuralPath,
    context: _SourceContext,
) -> str:
    if isinstance(node, ScalarNode):
        return _source_scalar(node, context.imports)
    if isinstance(node, LiteralNode):
        context.imports.typing_names.add("Literal")
        return f"Literal[{json.dumps(node.value, ensure_ascii=False)}]"
    if isinstance(node, AnyNode):
        context.imports.typing_names.add("Any")
        return "Any"
    if isinstance(node, ListNode):
        item = _source_annotation(
            node.item,
            suggested_name=f"{suggested_name}Item",
            path=(*path, "[]"),
            context=context,
        )
        if node.item_nullable:
            item = f"{item} | None"
        return f"list[{item}]"
    _collect_source_model(
        suggested_name,
        node,
        path=path,
        context=context,
    )
    return suggested_name


def _collect_source_model(
    name: str,
    node: ObjectNode,
    *,
    path: StructuralPath,
    context: _SourceContext,
) -> None:
    context.registry.register(name, path)
    used: dict[str, str] = {}
    source_fields: list[_SourceField] = []
    for field_contract in node.fields:
        mapped = pydantic_field_name(field_contract.name, used)
        nested_name = model_name(f"{name}_{field_contract.name}", "Structure")
        annotation = _source_annotation(
            field_contract.value,
            suggested_name=nested_name,
            path=(*path, field_contract.name),
            context=context,
        )
        if field_contract.nullable:
            annotation = f"{annotation} | None"
        if mapped.alias is not None or (
            not field_contract.required and not field_contract.nullable
        ):
            context.imports.field = True
        source_fields.append(
            _SourceField(
                mapped.python_name,
                annotation,
                field_contract.required,
                field_contract.nullable,
                mapped.alias,
            )
        )
    context.models.append(_SourceModel(name, tuple(source_fields)))


def _render_imports(imports: _SourceImports, *, has_models: bool) -> list[str]:
    standard: list[str] = []
    if imports.datetime_names:
        standard.append(f"from datetime import {', '.join(sorted(imports.datetime_names))}")
    if imports.decimal:
        standard.append("from decimal import Decimal")
    if imports.typing_names:
        standard.append(f"from typing import {', '.join(sorted(imports.typing_names))}")
    if imports.uuid:
        standard.append("from uuid import UUID")

    lines = list(standard)
    if has_models:
        pydantic_names = ["BaseModel", "ConfigDict"]
        if imports.field:
            pydantic_names.append("Field")
        if lines:
            lines.append("")
        lines.append(f"from pydantic import {', '.join(pydantic_names)}")
    return lines


def _render_field(field_contract: _SourceField) -> str:
    line = f"    {field_contract.name}: {field_contract.annotation}"
    if field_contract.alias is not None:
        alias = json.dumps(field_contract.alias, ensure_ascii=False)
        if field_contract.required:
            return f"{line} = Field(alias={alias})"
        return f"{line} = Field(default=None, alias={alias})"
    if field_contract.required:
        return line
    if field_contract.nullable:
        return f"{line} = None"
    return f"{line} = Field(default=None)"


def render_pydantic_source(contracts: tuple[TypeContract, ...]) -> str:
    """Render deterministic importable Pydantic v2 source from shared contracts."""
    context = _SourceContext()
    for contract in contracts:
        _collect_source_model(
            contract.model_name,
            contract.root,
            path=(contract.concept_type,),
            context=context,
        )

    lines = _render_imports(context.imports, has_models=bool(context.models))
    for model in context.models:
        lines.extend(["", "", f"class {model.name}(BaseModel):"])
        lines.append('    model_config = ConfigDict(extra="allow")')
        if model.fields:
            lines.append("")
            lines.extend(_render_field(item) for item in model.fields)
        else:
            lines.append("    pass")
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "PydanticFieldName",
    "PydanticModelNameRegistry",
    "build_dynamic_pydantic_models",
    "pydantic_field_name",
    "render_pydantic_source",
]
