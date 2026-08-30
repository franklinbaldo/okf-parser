"""Compile resolved RFC 0018 projections into schema contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.projections import ProjectionError
from okf_parser.schema_contract import (
    FieldContract,
    ListNode,
    ObjectNode,
    RefNode,
    ScalarNode,
    TypeContract,
    unique_model_names,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from okf_parser.projections import Projection, ProjectionMember


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
    """Compile projections from existing root and sibling contracts.

    The root contract is copied as-is because RFC 0018 v1 does not permit
    field exclusion. Declared members are appended as named references, never
    by copying sibling structure. ``optional: true`` makes the member nullable
    while the member remains part of the declared projection shape.
    """
    contracts_by_type: dict[str, TypeContract] = {}
    for contract in concept_contracts:
        contracts_by_type[contract.concept_type] = contract

    projection_names = tuple(projection.name for projection in projections)
    names = unique_model_names(projection_names, "Projection")
    compiled: list[TypeContract] = []

    for projection in projections:
        root_contract = contracts_by_type.get(projection.root)
        if root_contract is None:
            message = (
                f"projection {projection.name!r}: root contract "
                f"{projection.root!r} was not compiled"
            )
            raise ProjectionError(message)

        root_names = {field.name for field in root_contract.root.fields}
        members: list[FieldContract] = []
        for member in projection.members:
            if member.alias in root_names:
                message = (
                    f"projection {projection.name!r}: member {member.alias!r} "
                    "collides with a field of the root contract"
                )
                raise ProjectionError(message)

            reference = _projection_reference(member)
            if member.collection:
                value = ListNode(item=reference, item_nullable=False)
            else:
                value = reference
            members.append(
                FieldContract(
                    name=member.alias,
                    required=True,
                    nullable=member.optional,
                    value=value,
                )
            )

        root_fields = (*root_contract.root.fields, *members)
        compiled.append(
            TypeContract(
                concept_type=projection.name,
                model_name=names[projection.name],
                root=ObjectNode(root_fields),
            )
        )

    return tuple(compiled)


__all__ = ["compile_projections"]
