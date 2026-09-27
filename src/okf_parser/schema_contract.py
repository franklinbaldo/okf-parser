"""The schema contract IR the binary compiles, as Python dataclasses.

The native binary (``okf-db/src/schema``) compiles observations, declared
schemas, relational references, and projections into contracts and renders
JSON Schema, Zod, and GraphQL SDL. Python keeps only what cannot exist
outside Python: Pydantic models and source. This module decodes the binary's
contract JSON into the IR those renderers walk.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from okf_parser.duckdb_types import DuckDBLogicalType, logical_type_from_catalog

type CastKind = Literal["string", "boolean", "integer", "number", "date", "datetime"]
type ZodImport = Literal["zod", "astro"]
type RefsMode = Literal["key", "embed"]
type ContractNode = ScalarNode | LiteralNode | ObjectNode | ListNode | AnyNode | RefNode


class SchemaExportError(ValueError):
    """Base error for schema generation failures."""


class SchemaCastError(SchemaExportError):
    """Raised when an explicit schema cast is invalid or cannot be applied."""


class SchemaNameCollisionError(SchemaExportError):
    """Raised when distinct concept types normalize to the same generated name."""


class SchemaReferenceError(SchemaExportError):
    """Report a declared foreign key that the compiled contracts cannot carry."""


class ProjectionError(SchemaExportError):
    """Report a projection document the relational contract cannot support."""


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


class _Wire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _WireType(_Wire):
    sql: str
    family: str
    precision: int | None = None
    scale: int | None = None
    element: _WireType | None = None


class _WireScalar(_Wire):
    node: Literal["scalar"]
    kind: CastKind
    declared_type: _WireType | None = None


class _WireLiteral(_Wire):
    node: Literal["literal"]
    value: str


class _WireAny(_Wire):
    node: Literal["any"]


class _WireList(_Wire):
    node: Literal["list"]
    item: _WireNode
    item_nullable: bool
    declared_type: _WireType | None = None


class _WireRef(_Wire):
    node: Literal["ref"]
    concept_type: str
    columns: tuple[str, ...]
    referenced_columns: tuple[str, ...]
    position: int
    value: _WireNode
    embedded: bool


class _WireField(_Wire):
    name: str
    required: bool
    nullable: bool
    value: _WireNode


class _WireObject(_Wire):
    node: Literal["object"]
    fields: tuple[_WireField, ...]


type _WireNode = Annotated[
    _WireScalar | _WireLiteral | _WireAny | _WireList | _WireRef | _WireObject,
    Field(discriminator="node"),
]


class _WireContract(_Wire):
    concept_type: str
    model_name: str
    fields: tuple[_WireField, ...]


class _WireContracts(_Wire):
    contracts: tuple[_WireContract, ...]


def _logical_type(wire: _WireType) -> DuckDBLogicalType:
    # The catalog classifier is the one source of the family; the binary's
    # precision and scale are DuckDB's own and win over the spelling.
    return logical_type_from_catalog(
        wire.sql, numeric_precision=wire.precision, numeric_scale=wire.scale
    )


def _field(wire: _WireField) -> FieldContract:
    return FieldContract(wire.name, wire.required, wire.nullable, _node(wire.value))


def _node(wire: _WireNode) -> ContractNode:
    match wire:
        case _WireScalar():
            declared = None if wire.declared_type is None else _logical_type(wire.declared_type)
            return ScalarNode(wire.kind, declared)
        case _WireLiteral():
            return LiteralNode(wire.value)
        case _WireAny():
            return AnyNode()
        case _WireList():
            declared = None if wire.declared_type is None else _logical_type(wire.declared_type)
            return ListNode(_node(wire.item), wire.item_nullable, declared)
        case _WireRef():
            return RefNode(
                concept_type=wire.concept_type,
                columns=wire.columns,
                referenced_columns=wire.referenced_columns,
                position=wire.position,
                value=_node(wire.value),
                embedded=wire.embedded,
            )
        case _WireObject():
            return ObjectNode(tuple(_field(field) for field in wire.fields))


def contracts_from_json(payload: object) -> tuple[TypeContract, ...]:
    """Decode the binary's ``{"contracts": [...]}`` answer into the IR."""
    wire = _WireContracts.model_validate(payload)
    return tuple(
        TypeContract(
            concept_type=contract.concept_type,
            model_name=contract.model_name,
            root=ObjectNode(tuple(_field(field) for field in contract.fields)),
        )
        for contract in wire.contracts
    )


__all__ = [
    "AnyNode",
    "CastKind",
    "ContractNode",
    "FieldContract",
    "ListNode",
    "LiteralNode",
    "ObjectNode",
    "ProjectionError",
    "RefNode",
    "RefsMode",
    "ScalarNode",
    "SchemaCastError",
    "SchemaExportError",
    "SchemaNameCollisionError",
    "SchemaReferenceError",
    "TypeContract",
    "ZodImport",
    "contracts_from_json",
    "model_name",
]
