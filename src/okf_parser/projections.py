"""Parse, resolve, and compile `type: Projection` documents.

RFC 0018 section 5. A projection names one root concept type and the declared
relations to traverse; every relation it lists must already exist as a
``FOREIGN KEY`` in ``okf.schema.sql``, so a projection composes what the bundle
declares and can never invent a relationship. Projections do not become concept
types: they have no documents of their own, they are not materialized as typed
relations, and they do not participate in ``apply``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast

from okf_parser.relational_schema import load_relational_schema
from okf_parser.schema_contract import (
    FieldContract,
    ListNode,
    ObjectNode,
    RefNode,
    ScalarNode,
    SchemaExportError,
    TypeContract,
    unique_model_names,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from okf_parser.relational_schema import ForeignKeyConstraint

PROJECTION_TYPE = "Projection"
"""The authored `type` of a projection document."""

_MEMBER_KEYS = frozenset({"relation", "as", "optional"})


class ProjectionError(SchemaExportError):
    """Report a projection document the relational contract cannot support."""


def _fail(message: str) -> NoReturn:
    raise ProjectionError(message)


@dataclass(frozen=True, slots=True)
class ProjectionMember:
    """One declared relation of a projection, resolved to its foreign key."""

    alias: str
    relation: str
    concept_type: str
    foreign_key: ForeignKeyConstraint
    collection: bool
    optional: bool


@dataclass(frozen=True, slots=True)
class Projection:
    """A composed shape: one root concept type plus its declared members."""

    name: str
    root: str
    members: tuple[ProjectionMember, ...]


def _text(document: Mapping[str, object], key: str, missing: str) -> str:
    value = document.get(key)
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        _fail(missing)
    return text


def _member_mappings(
    document: Mapping[str, object], name: str
) -> tuple[Mapping[str, object], ...]:
    include = document.get("include", [])
    if include is None:
        return ()
    if not isinstance(include, list):
        _fail(f"projection {name!r}: include must be a list of members")
    members: list[Mapping[str, object]] = []
    for entry in include:
        if not isinstance(entry, dict):
            _fail(
                f"projection {name!r}: include member must be a mapping, got {entry!r}"
            )
        members.append(cast("Mapping[str, object]", entry))
    return tuple(members)


def _split_relation(relation: str, name: str) -> tuple[str, str]:
    from_type, separator, column = relation.partition(".")
    if not separator or not from_type.strip() or not column.strip():
        message = (
            f"projection {name!r}: relation {relation!r} must be written "
            f"'FromType.field', naming a declared foreign key"
        )
        _fail(message)
    return from_type.strip(), column.strip()


def _resolve_foreign_key(
    from_type: str,
    column: str,
    relation: str,
    name: str,
    foreign_keys: Sequence[ForeignKeyConstraint],
) -> ForeignKeyConstraint:
    matches = [
        item
        for item in foreign_keys
        if item.table == from_type and column in item.columns
    ]
    if not matches:
        message = (
            f"projection {name!r}: the relational contract does not declare a foreign "
            f"key for {relation!r}; a projection cannot invent a relationship"
        )
        _fail(message)
    if len(matches) > 1:
        named = ", ".join(sorted(item.name for item in matches))
        message = (
            f"projection {name!r}: {relation!r} is ambiguous, it participates in "
            f"more than one declared foreign key ({named})"
        )
        _fail(message)
    return matches[0]


def _optional_flag(entry: Mapping[str, object], relation: str, name: str) -> bool:
    value = entry.get("optional", False)
    if not isinstance(value, bool):
        message = (
            f"projection {name!r}: {relation!r} optional must be a boolean, got {value!r}"
        )
        _fail(message)
    return value


def _member(
    entry: Mapping[str, object],
    name: str,
    root: str,
    foreign_keys: Sequence[ForeignKeyConstraint],
) -> ProjectionMember:
    unrecognized = sorted(set(entry) - _MEMBER_KEYS)
    if unrecognized:
        message = (
            f"projection {name!r}: unrecognized member key(s) {', '.join(unrecognized)}; "
            f"a projection declares composition only"
        )
        _fail(message)
    relation = _text(entry, "relation", f"projection {name!r}: member has no relation")
    alias = _text(
        entry,
        "as",
        f"projection {name!r}: member {relation!r} has no 'as' name",
    )
    from_type, column = _split_relation(relation, name)
    foreign_key = _resolve_foreign_key(from_type, column, relation, name, foreign_keys)
    # RFC 0007 makes N:1 the primitive, so a key *pointing at* the root is the
    # root's 1:N list, and a key *on* the root is a single value. A
    # self-reference is written from the root's own side and so reads as single.
    collection = foreign_key.table != root
    if collection and foreign_key.referenced_table != root:
        message = (
            f"projection {name!r}: {relation!r} connects {foreign_key.referenced_table} "
            f"to {foreign_key.table}, not {root}"
        )
        _fail(message)
    return ProjectionMember(
        alias=alias,
        relation=relation,
        concept_type=foreign_key.table if collection else foreign_key.referenced_table,
        foreign_key=foreign_key,
        collection=collection,
        optional=_optional_flag(entry, relation, name),
    )


def _members(
    document: Mapping[str, object],
    name: str,
    root: str,
    foreign_keys: Sequence[ForeignKeyConstraint],
) -> tuple[ProjectionMember, ...]:
    members = tuple(
        _member(entry, name, root, foreign_keys)
        for entry in _member_mappings(document, name)
    )
    seen: set[str] = set()
    for member in members:
        if member.alias in seen:
            _fail(f"projection {name!r} declares {member.alias!r} twice")
        seen.add(member.alias)
    return members


def _projection(
    document: Mapping[str, object],
    foreign_keys: Sequence[ForeignKeyConstraint],
    concept_types: Sequence[str],
) -> Projection:
    name = _text(document, "name", "projection document has no name")
    if name in concept_types:
        message = (
            f"projection name {name!r} collides with concept type {name!r}; "
            f"a projection is not a concept type"
        )
        _fail(message)
    root = _text(document, "root", f"projection {name!r} has no root")
    if root not in concept_types:
        message = f"projection {name!r}: root {root!r} is an unknown concept type"
        _fail(message)
    return Projection(
        name=name,
        root=root,
        members=_members(document, name, root, foreign_keys),
    )


def parse_projections(
    documents: Sequence[Mapping[str, object]],
    foreign_keys: Sequence[ForeignKeyConstraint],
    *,
    concept_types: Sequence[str],
) -> tuple[Projection, ...]:
    """Resolve projection frontmatter against the bundle's declared foreign keys.

    `concept_types` is the set of types the bundle actually has documents for;
    it is what makes an unknown root and a name collision detectable. Results
    are ordered by name so regeneration keeps producing an empty diff.
    """
    projections = sorted(
        (_projection(document, foreign_keys, concept_types) for document in documents),
        key=lambda item: item.name,
    )
    seen: set[str] = set()
    for projection in projections:
        if projection.name in seen:
            _fail(f"projection {projection.name!r} is declared twice")
        seen.add(projection.name)
    return tuple(projections)


def load_projections(
    path: str,
    exclude: Sequence[str] = (),
    *,
    relational_schema: str | None,
) -> tuple[Projection, ...]:
    """Read a bundle's projection documents and resolve them.

    A bundle with no projection document yields nothing, whether or not it
    declares a relational schema. A bundle that declares projections without a
    relational schema is an error: there is nothing to resolve them against.
    """
    # Deferred: `schema_export` imports this module to keep projection documents
    # out of the compiled concept types.
    from okf_parser.schema_export import documents_by_type  # noqa: PLC0415

    observed = documents_by_type(path, exclude)
    documents = observed.get(PROJECTION_TYPE, [])
    if not documents:
        return ()
    if relational_schema is None:
        message = (
            f"bundle declares {len(documents)} projection document(s) but no relational "
            f"schema; pass --relational-schema so the relations can be resolved"
        )
        raise ProjectionError(message)
    declared = Path(relational_schema)
    resolved = declared if declared.is_absolute() else Path(path) / declared
    schema = load_relational_schema(resolved)
    concept_types = tuple(name for name in observed if name != PROJECTION_TYPE)
    return parse_projections(documents, schema.foreign_keys, concept_types=concept_types)


def _projection_reference(member: ProjectionMember) -> RefNode:
    """Represent one composed member as a named sibling-schema reference."""
    return RefNode(
        concept_type=member.concept_type,
        columns=member.foreign_key.columns,
        referenced_columns=member.foreign_key.referenced_columns,
        position=0,
        value=ScalarNode("string"),
        embedded=True,
    )


def compile_projections(
    projections: Sequence[Projection],
    concept_contracts: Sequence[TypeContract],
) -> tuple[TypeContract, ...]:
    """Compile resolved projections from existing concept contracts.

    The root contract is copied as-is: v1 does not permit projection field
    exclusion. Declared members are appended as named references to sibling
    contracts, never by copying the sibling structure. A member is always
    present; RFC 0018's ``optional`` modifier makes its value nullable.
    """
    contracts_by_type = {
        contract.concept_type: contract for contract in concept_contracts
    }
    names = unique_model_names(tuple(item.name for item in projections), "Projection")
    compiled: list[TypeContract] = []
    for projection in projections:
        root_contract = contracts_by_type.get(projection.root)
        if root_contract is None:
            _fail(
                f"projection {projection.name!r}: root contract {projection.root!r} "
                "was not compiled"
            )
        root_names = {field.name for field in root_contract.root.fields}
        members: list[FieldContract] = []
        for member in projection.members:
            if member.alias in root_names:
                _fail(
                    f"projection {projection.name!r}: member {member.alias!r} collides "
                    "with a field of the root contract"
                )
            reference = _projection_reference(member)
            value = (
                ListNode(item=reference, item_nullable=False)
                if member.collection
                else reference
            )
            members.append(
                FieldContract(
                    name=member.alias,
                    required=True,
                    nullable=member.optional,
                    value=value,
                )
            )
        compiled.append(
            TypeContract(
                concept_type=projection.name,
                model_name=names[projection.name],
                root=ObjectNode((*root_contract.root.fields, *members)),
            )
        )
    return tuple(compiled)


__all__ = [
    "PROJECTION_TYPE",
    "Projection",
    "ProjectionError",
    "ProjectionMember",
    "compile_projections",
    "load_projections",
    "parse_projections",
]
