"""Optional read-only GraphQL projection over canonical OKF relations and TypeContract.

The binary renders the SDL and names every GraphQL type and field
(``okf-db/src/schema/graphql.rs``); this module only makes it executable,
resolving concepts from one bundle snapshot.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from okf_parser.bundle import Bundle, load_bundle
from okf_parser.schema_contract import (
    ContractNode,
    FieldContract,
    ListNode,
    ScalarNode,
    SchemaExportError,
    contracts_from_json,
)
from okf_parser.schema_export import SCHEMA_ERRORS, SchemaRequest, native_schema

if TYPE_CHECKING:
    from types import ModuleType

    from graphql import GraphQLSchema

_GRAPHQL_INSTALL_MESSAGE = (
    "GraphQL execution requires the optional dependency; install okf-parser[graphql]"
)
_MAX_PAGE_SIZE = 1000
_PAGINATION_MESSAGE = "GraphQL pagination requires 0 <= offset and 1 <= first <= 1000"
_SMALL_INTEGERS = frozenset({"TINYINT", "SMALLINT", "INTEGER", "UTINYINT", "USMALLINT"})


class GraphQLAdapterUnavailableError(RuntimeError):
    """Raised when executable GraphQL support is requested without its extra."""


class GraphQLNameCollisionError(SchemaExportError):
    """Raised when distinct OKF structural paths map to one GraphQL name."""


@dataclass(frozen=True, slots=True)
class GraphQLResult:
    """JSON-ready result of one read-only GraphQL execution."""

    data: dict[str, object] | None
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ProjectedField:
    original_name: str
    graphql_name: str
    contract: FieldContract


@dataclass(frozen=True, slots=True)
class _Projection:
    sdl: str
    type_names: dict[str, str]
    fields: dict[str, tuple[_ProjectedField, ...]]


class _GraphqlType(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    fields: dict[str, str]


class _GraphqlAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sdl: str
    types: dict[str, _GraphqlType]
    contracts: list[object]


def _graphql_module() -> ModuleType:
    try:
        return import_module("graphql")
    except ModuleNotFoundError as exc:
        raise GraphQLAdapterUnavailableError(_GRAPHQL_INSTALL_MESSAGE) from exc


def _native_projection(
    path: str,
    exclude: Sequence[str],
    *,
    infer_types: bool,
    casts: Sequence[str],
    spec_template: str | None,
) -> _Projection:
    result = native_schema(
        SchemaRequest(
            path=path,
            target="graphql",
            exclude=tuple(exclude),
            infer_types=infer_types,
            casts=tuple(casts),
            spec_template=spec_template,
        ),
        {**SCHEMA_ERRORS, "graphql_name_collision": GraphQLNameCollisionError},
    )
    answer = _GraphqlAnswer.model_validate(result)
    contracts = contracts_from_json({"contracts": answer.contracts})
    type_names: dict[str, str] = {}
    fields: dict[str, tuple[_ProjectedField, ...]] = {}
    for contract in contracts:
        graphql_type = answer.types[contract.concept_type]
        type_names[contract.concept_type] = graphql_type.name
        by_original = {field.name: field for field in contract.root.fields}
        fields[contract.concept_type] = tuple(
            _ProjectedField(original, graphql_name, by_original[original])
            for graphql_name, original in graphql_type.fields.items()
        )
    return _Projection(answer.sdl, type_names, fields)


def _scalar_graphql_type(node: ScalarNode) -> str:
    declared = node.declared_type
    if declared is not None:
        if declared.family == "integer":
            return "Int" if declared.sql.upper() in _SMALL_INTEGERS else "BigInt"
        declared_types = {
            "string": "String",
            "boolean": "Boolean",
            "float": "Float",
            "decimal": "Decimal",
            "date": "Date",
            "timestamp": "DateTime",
            "timestamptz": "DateTime",
            "uuid": "UUID",
        }
        return declared_types.get(declared.family, "JSON")
    observed_types = {
        "string": "String",
        "boolean": "Boolean",
        "integer": "BigInt",
        "number": "Float",
        "date": "Date",
        "datetime": "DateTime",
    }
    return observed_types[node.kind]


def export_graphql_sdl(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    spec_template: str | None = None,
) -> str:
    """Export deterministic GraphQL SDL without requiring the GraphQL runtime extra."""
    return _native_projection(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
    ).sdl


def _typed_values(
    bundle: Bundle,
    spec_template: str | None,
) -> dict[str, dict[str, dict[str, object]]]:
    if spec_template is None:
        return {}
    values: dict[str, dict[str, dict[str, object]]] = {}
    tables = bundle.sql(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'okf_types' ORDER BY table_name",
        spec_template=spec_template,
    )
    for (concept_type,) in tables:
        quoted = '"' + str(concept_type).replace('"', '""') + '"'
        rows = bundle.sql(f"FROM okf_types.{quoted}", spec_template=spec_template).to_dicts()
        by_path: dict[str, dict[str, object]] = {}
        for row in rows:
            path = row.get("__okf_path")
            if isinstance(path, str):
                by_path[path] = row
        values[str(concept_type)] = by_path
    return values


def _json_ready(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        result = value
    elif isinstance(value, float):
        result = None if math.isnan(value) else value
    elif isinstance(value, Decimal):
        result = str(value)
    elif isinstance(value, (date, datetime)):
        result = value.isoformat()
    elif isinstance(value, UUID):
        result = str(value)
    elif isinstance(value, Mapping):
        result = {str(key): _json_ready(item) for key, item in value.items()}
    elif isinstance(value, (list, tuple)):
        result = [_json_ready(item) for item in value]
    else:
        result = str(value)
    return result


def _graphql_value(value: object, node: ContractNode) -> object:
    if value is None:
        return None
    if isinstance(node, ListNode):
        if not isinstance(value, (list, tuple)):
            return _json_ready(value)
        return [_graphql_value(item, node.item) for item in value]
    result = _json_ready(value)
    if isinstance(node, ScalarNode):
        scalar = _scalar_graphql_type(node)
        if scalar == "BigInt":
            result = str(value)
        elif scalar == "Date" and isinstance(value, datetime):
            result = value.date().isoformat()
        elif scalar == "Date" and isinstance(value, date):
            result = value.isoformat()
    return result


class _Runtime:
    def __init__(
        self,
        bundle: Bundle,
        projection: _Projection,
        typed_values: dict[str, dict[str, dict[str, object]]],
    ) -> None:
        self.bundle = bundle
        self.projection = projection
        self.typed_values = typed_values
        self.links_by_source: dict[str, list[dict[str, object]]] = {}
        self.links_by_target: dict[str, list[dict[str, object]]] = {}
        for row in (link.model_dump() for link in bundle.links):
            link = {
                "sourceId": row["source_id"],
                "rawTarget": row["raw_target"],
                "targetId": row["target_id"] if isinstance(row["target_id"], str) else None,
                "exists": bool(row["exists"]),
                "origin": row["origin"],
            }
            source_id = str(row["source_id"])
            self.links_by_source.setdefault(source_id, []).append(link)
            target_id = row["target_id"]
            if isinstance(target_id, str):
                self.links_by_target.setdefault(target_id, []).append(link)
        self.diagnostics_by_path: dict[str, list[dict[str, object]]] = {}
        for diagnostic in bundle.validate():
            self.diagnostics_by_path.setdefault(diagnostic.path, []).append(
                {
                    "code": diagnostic.code,
                    "severity": diagnostic.severity.value,
                    "path": diagnostic.path,
                    "message": diagnostic.message,
                }
            )

    def _record(self, row: Mapping[str, object]) -> dict[str, object]:
        concept_id = str(row["concept_id"])
        concept_type = str(row["concept_type"])
        path = str(row["path"])
        raw_frontmatter = row.get("frontmatter_json")
        frontmatter_object = json.loads(raw_frontmatter) if isinstance(raw_frontmatter, str) else {}
        frontmatter = dict(frontmatter_object) if isinstance(frontmatter_object, dict) else {}
        typed = self.typed_values.get(concept_type, {}).get(path, {})
        record: dict[str, object] = {
            "__typename": self.projection.type_names[concept_type],
            "id": concept_id,
            "logicalKey": (
                row.get("logical_key") if isinstance(row.get("logical_key"), str) else None
            ),
            "path": path,
            "type": concept_type,
            "title": row.get("title") if isinstance(row.get("title"), str) else None,
            "description": (
                row.get("description") if isinstance(row.get("description"), str) else None
            ),
            "sourceDigest": str(row["source_digest"]),
            "parsedDigest": str(row["parsed_digest"]),
            "body": str(row["body"]),
            "frontmatter": _json_ready(frontmatter),
            "links": self.links_by_source.get(concept_id, []),
            "reverseLinks": self.links_by_target.get(concept_id, []),
            "diagnostics": self.diagnostics_by_path.get(path, []),
        }
        for projected in self.projection.fields.get(concept_type, ()):
            value = typed.get(projected.original_name, frontmatter.get(projected.original_name))
            record[projected.graphql_name] = _graphql_value(value, projected.contract.value)
        return record

    @staticmethod
    def _pagination(arguments: Mapping[str, object]) -> tuple[int, int]:
        first_raw = arguments.get("first", 50)
        offset_raw = arguments.get("offset", 0)
        first = first_raw if isinstance(first_raw, int) else 50
        offset = offset_raw if isinstance(offset_raw, int) else 0
        if first < 1 or first > _MAX_PAGE_SIZE or offset < 0:
            raise ValueError(_PAGINATION_MESSAGE)
        return first, offset

    def resolve_concept(
        self,
        _root: object,
        _info: object,
        **arguments: object,
    ) -> dict[str, object] | None:
        """Resolve one canonical concept by exact ID."""
        concept_id = arguments.get("id")
        if not isinstance(concept_id, str):
            return None
        matches = [
            concept.model_dump()
            for concept in self.bundle.concepts
            if concept.concept_id == concept_id
        ]
        return self._record(matches[0]) if matches else None

    def resolve_concepts(
        self,
        _root: object,
        _info: object,
        **arguments: object,
    ) -> list[dict[str, object]]:
        """Resolve canonical concepts with deterministic bounded pagination."""
        first, offset = self._pagination(arguments)
        concept_type = arguments.get("type")
        selected = sorted(
            (
                concept
                for concept in self.bundle.concepts
                if not isinstance(concept_type, str) or concept.concept_type == concept_type
            ),
            key=lambda concept: concept.concept_id,
        )
        return [self._record(concept.model_dump()) for concept in selected[offset : offset + first]]


def _build_executable_schema(sdl: str, runtime: _Runtime) -> GraphQLSchema:
    module = _graphql_module()
    schema = module.build_schema(sdl)
    query_type = schema.get_type("Query")
    concept_type = schema.get_type("Concept")
    if query_type is None or concept_type is None:
        message = "generated GraphQL schema is missing Query or Concept"
        raise RuntimeError(message)
    query_type.fields["concept"].resolve = runtime.resolve_concept
    query_type.fields["concepts"].resolve = runtime.resolve_concepts

    def resolve_type(value: Mapping[str, object], _info: object, _abstract: object) -> object:
        return value.get("__typename")

    concept_type.resolve_type = resolve_type
    return schema


class GraphQLReadAdapter:
    """Embedded executable read-only GraphQL schema over one OKF bundle snapshot."""

    def __init__(
        self,
        path: str,
        exclude: Sequence[str] = (),
        *,
        infer_types: bool = False,
        casts: Sequence[str] = (),
        spec_template: str | None = None,
    ) -> None:
        """Build a host-embeddable read-only schema for one OKF bundle snapshot."""
        bundle = load_bundle(Path(path), exclude)
        projection = _native_projection(
            path,
            exclude,
            infer_types=infer_types,
            casts=casts,
            spec_template=spec_template,
        )
        runtime = _Runtime(bundle, projection, _typed_values(bundle, spec_template))
        self._schema = _build_executable_schema(projection.sdl, runtime)

    @property
    def schema(self) -> GraphQLSchema:
        """Return the embedded GraphQLSchema for host-owned transport integration."""
        return self._schema

    def execute(
        self,
        query: str,
        variables: Mapping[str, object] | None = None,
    ) -> GraphQLResult:
        """Execute one read-only query and return JSON-ready data and errors."""
        module = _graphql_module()
        result = module.graphql_sync(
            self._schema,
            query,
            variable_values=dict(variables) if variables is not None else None,
        )
        data = result.data if isinstance(result.data, dict) else None
        errors = tuple(str(error) for error in (result.errors or ()))
        return GraphQLResult(data=data, errors=errors)


def build_graphql_schema(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    spec_template: str | None = None,
) -> GraphQLSchema:
    """Build an executable read-only GraphQLSchema without starting an HTTP server."""
    return GraphQLReadAdapter(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
    ).schema


__all__ = [
    "GraphQLAdapterUnavailableError",
    "GraphQLNameCollisionError",
    "GraphQLReadAdapter",
    "GraphQLResult",
    "build_graphql_schema",
    "export_graphql_sdl",
]
