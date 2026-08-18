"""Optional read-only GraphQL projection over canonical OKF relations and TypeContract."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from okf_parser.bundle import Bundle, load_bundle
from okf_parser.schema_contract import (
    AnyNode,
    ContractNode,
    FieldContract,
    ListNode,
    LiteralNode,
    ObjectNode,
    ScalarNode,
    SchemaExportError,
    TypeContract,
)
from okf_parser.schema_export import build_schema_contracts

if TYPE_CHECKING:
    from types import ModuleType

    from graphql import GraphQLSchema

_GRAPHQL_NAME_RE = re.compile(r"^[_A-Za-z][_0-9A-Za-z]*$")
_GENERIC_FIELDS = frozenset(
    {
        "id",
        "logicalKey",
        "path",
        "type",
        "title",
        "description",
        "sourceDigest",
        "parsedDigest",
        "body",
        "frontmatter",
        "links",
        "reverseLinks",
        "diagnostics",
    }
)
_GRAPHQL_INSTALL_MESSAGE = (
    "GraphQL execution requires the optional dependency; install okf-parser[graphql]"
)
_MAX_PAGE_SIZE = 1000
_PAGINATION_MESSAGE = "GraphQL pagination requires 0 <= offset and 1 <= first <= 1000"


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
    type_names: dict[str, str]
    fields: dict[str, tuple[_ProjectedField, ...]]


def _graphql_module() -> ModuleType:
    try:
        return import_module("graphql")
    except ModuleNotFoundError as exc:
        raise GraphQLAdapterUnavailableError(_GRAPHQL_INSTALL_MESSAGE) from exc


def _ascii_identifier(value: str, prefix: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    identifier = re.sub(r"[^_0-9A-Za-z]", "_", ascii_value)
    identifier = re.sub(r"_+", "_", identifier).strip("_")
    if not identifier:
        identifier = prefix
    if identifier[0].isdigit():
        identifier = f"{prefix}_{identifier}"
    if identifier.startswith("__"):
        identifier = f"{prefix}_{identifier.lstrip('_') or prefix}"
    return identifier


def _type_graphql_name(contract: TypeContract) -> str:
    return _ascii_identifier(contract.model_name, "Concept")


def _field_graphql_name(field_name: str) -> str:
    if (
        _GRAPHQL_NAME_RE.fullmatch(field_name) is not None
        and field_name not in _GENERIC_FIELDS
        and not field_name.startswith("__")
    ):
        return field_name
    return f"field_{_ascii_identifier(field_name, 'value')}"


def _projection(contracts: Sequence[TypeContract]) -> _Projection:
    type_names: dict[str, str] = {}
    type_owners: dict[str, str] = {}
    fields: dict[str, tuple[_ProjectedField, ...]] = {}

    for contract in contracts:
        type_name = _type_graphql_name(contract)
        previous_type = type_owners.get(type_name)
        if previous_type is not None and previous_type != contract.concept_type:
            message = (
                f"concept types {previous_type!r} and {contract.concept_type!r} "
                f"both map to GraphQL type {type_name!r}"
            )
            raise GraphQLNameCollisionError(message)
        type_owners[type_name] = contract.concept_type
        type_names[contract.concept_type] = type_name

        projected: list[_ProjectedField] = []
        field_owners: dict[str, str] = {}
        for field_contract in contract.root.fields:
            if field_contract.name in {"type", "title", "description"}:
                continue
            graphql_name = _field_graphql_name(field_contract.name)
            previous_field = field_owners.get(graphql_name)
            if previous_field is not None and previous_field != field_contract.name:
                message = (
                    f"fields {contract.concept_type}.{previous_field} and "
                    f"{contract.concept_type}.{field_contract.name} both map to "
                    f"GraphQL field {type_name}.{graphql_name}"
                )
                raise GraphQLNameCollisionError(message)
            field_owners[graphql_name] = field_contract.name
            projected.append(_ProjectedField(field_contract.name, graphql_name, field_contract))
        fields[contract.concept_type] = tuple(projected)

    return _Projection(type_names, fields)


def _is_graphql_int(node: ScalarNode) -> bool:
    declared = node.declared_type
    if declared is None:
        return False
    return declared.family == "integer" and declared.sql.upper() in {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "UTINYINT",
        "USMALLINT",
    }


def _scalar_graphql_type(node: ScalarNode) -> str:
    declared = node.declared_type
    if declared is not None:
        if declared.family == "integer":
            return "Int" if _is_graphql_int(node) else "BigInt"
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


def _node_graphql_type(node: ContractNode) -> str:
    if isinstance(node, ScalarNode):
        return _scalar_graphql_type(node)
    if isinstance(node, LiteralNode):
        return "String"
    if isinstance(node, ListNode):
        item = _node_graphql_type(node.item)
        if not node.item_nullable:
            item = f"{item}!"
        return f"[{item}]"
    if isinstance(node, (AnyNode, ObjectNode)):
        return "JSON"
    message = f"unsupported GraphQL contract node: {type(node).__name__}"
    raise TypeError(message)


def _field_graphql_type(field_contract: FieldContract) -> str:
    rendered = _node_graphql_type(field_contract.value)
    if field_contract.required and not field_contract.nullable:
        return f"{rendered}!"
    return rendered


def render_graphql_sdl(contracts: Sequence[TypeContract]) -> str:
    """Render deterministic read-only GraphQL SDL from shared TypeContract objects."""
    projection = _projection(contracts)
    lines = [
        "scalar JSON",
        "scalar BigInt",
        "scalar Decimal",
        "scalar Date",
        "scalar DateTime",
        "scalar UUID",
        "",
        "directive @okfType(name: String!) on OBJECT",
        "directive @okfField(name: String!) on FIELD_DEFINITION",
        "",
        "type Link {",
        "  sourceId: ID!",
        "  rawTarget: String!",
        "  targetId: ID",
        "  exists: Boolean!",
        "  origin: String!",
        "}",
        "",
        "type Diagnostic {",
        "  code: String!",
        "  severity: String!",
        "  path: String!",
        "  message: String!",
        "}",
        "",
        "interface Concept {",
        "  id: ID!",
        "  logicalKey: String",
        "  path: String!",
        "  type: String!",
        "  title: String",
        "  description: String",
        "  sourceDigest: String!",
        "  parsedDigest: String!",
        "  body: String!",
        "  frontmatter: JSON!",
        "  links: [Link!]!",
        "  reverseLinks: [Link!]!",
        "  diagnostics: [Diagnostic!]!",
        "}",
        "",
    ]
    generic_lines = [
        "  id: ID!",
        "  logicalKey: String",
        "  path: String!",
        "  type: String!",
        "  title: String",
        "  description: String",
        "  sourceDigest: String!",
        "  parsedDigest: String!",
        "  body: String!",
        "  frontmatter: JSON!",
        "  links: [Link!]!",
        "  reverseLinks: [Link!]!",
        "  diagnostics: [Diagnostic!]!",
    ]
    for contract in contracts:
        type_name = projection.type_names[contract.concept_type]
        authored_type = json.dumps(contract.concept_type, ensure_ascii=False)
        lines.append(f"type {type_name} implements Concept @okfType(name: {authored_type}) {{")
        lines.extend(generic_lines)
        for projected in projection.fields[contract.concept_type]:
            field_type = _field_graphql_type(projected.contract)
            if projected.graphql_name == projected.original_name:
                lines.append(f"  {projected.graphql_name}: {field_type}")
            else:
                original = json.dumps(projected.original_name, ensure_ascii=False)
                lines.append(
                    f"  {projected.graphql_name}: {field_type} @okfField(name: {original})"
                )
        lines.extend(["}", ""])
    lines.extend(
        [
            "type Query {",
            "  concept(id: ID!): Concept",
            "  concepts(type: String, first: Int = 50, offset: Int = 0): [Concept!]!",
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def export_graphql_sdl(
    path: str,
    exclude: Sequence[str] = (),
    *,
    infer_types: bool = False,
    casts: Sequence[str] = (),
    spec_template: str | None = None,
) -> str:
    """Export deterministic GraphQL SDL without requiring the GraphQL runtime extra."""
    contracts = build_schema_contracts(
        path,
        exclude,
        infer_types=infer_types,
        casts=casts,
        spec_template=spec_template,
    )
    return render_graphql_sdl(contracts)


def _typed_values(
    bundle: Bundle,
    spec_template: str | None,
) -> dict[str, dict[str, dict[str, object]]]:
    if spec_template is None:
        return {}
    values: dict[str, dict[str, dict[str, object]]] = {}
    with bundle.compile_types(spec_template) as typed:
        for concept_type in typed:
            rows = typed[concept_type].execute().to_dict(orient="records")
            by_path: dict[str, dict[str, object]] = {}
            for row in rows:
                path = row.get("__okf_path")
                if isinstance(path, str):
                    by_path[path] = dict(row)
            values[concept_type] = by_path
    return values


def _json_ready(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        result = value
    elif isinstance(value, float):
        result = None if math.isnan(value) else value
    elif isinstance(value, Decimal):
        result = str(value)
    elif isinstance(value, datetime):
        result = value.isoformat()
    elif isinstance(value, date):
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
        for row in bundle.links.execute().to_dict(orient="records"):
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
            "logicalKey": row.get("logical_key") if isinstance(row.get("logical_key"), str) else None,
            "path": path,
            "type": concept_type,
            "title": row.get("title") if isinstance(row.get("title"), str) else None,
            "description": row.get("description") if isinstance(row.get("description"), str) else None,
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
        table = self.bundle.concepts
        rows = (
            table.filter(table["concept_id"] == concept_id)
            .order_by("concept_id")
            .limit(1)
            .execute()
            .to_dict(orient="records")
        )
        return self._record(rows[0]) if rows else None

    def resolve_concepts(
        self,
        _root: object,
        _info: object,
        **arguments: object,
    ) -> list[dict[str, object]]:
        """Resolve canonical concepts with deterministic bounded pagination."""
        first, offset = self._pagination(arguments)
        table = self.bundle.concepts
        concept_type = arguments.get("type")
        if isinstance(concept_type, str):
            table = table.filter(table["concept_type"] == concept_type)
        rows = (
            table.order_by("concept_id")
            .limit(first, offset=offset)
            .execute()
            .to_dict(orient="records")
        )
        return [self._record(row) for row in rows]


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
        contracts = build_schema_contracts(
            path,
            exclude,
            infer_types=infer_types,
            casts=casts,
            spec_template=spec_template,
        )
        projection = _projection(contracts)
        runtime = _Runtime(bundle, projection, _typed_values(bundle, spec_template))
        self._schema = _build_executable_schema(render_graphql_sdl(contracts), runtime)

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
    "render_graphql_sdl",
]
