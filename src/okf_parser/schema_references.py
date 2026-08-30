"""Compile declared foreign keys into reference nodes on the shared contracts.

RFC 0007 already gave the bundle one place to declare that two concept types
are related: ``okf.schema.sql``. RFC 0018 reads that same declaration from the
export side, so a field participating in a declared ``FOREIGN KEY`` compiles to
a :class:`~okf_parser.schema_contract.RefNode` instead of to the bare scalar it
carries. No naming or folder convention is consulted here, and no second place
to declare a relationship is introduced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.schema_contract import (
    FieldContract,
    ObjectNode,
    RefNode,
    SchemaExportError,
    TypeContract,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from okf_parser.relational_schema import ForeignKeyConstraint


class SchemaReferenceError(SchemaExportError):
    """Report a declared foreign key that the compiled contracts cannot carry."""


def _fail(message: str) -> None:
    raise SchemaReferenceError(message)


def _referenced_field(
    field_contract: FieldContract,
    foreign_key: ForeignKeyConstraint,
    position: int,
) -> FieldContract:
    if isinstance(field_contract.value, RefNode):
        message = (
            f"{foreign_key.table}.{field_contract.name} participates in more than one "
            f"declared foreign key; a column can carry only one reference"
        )
        _fail(message)
    reference = RefNode(
        concept_type=foreign_key.referenced_table,
        columns=foreign_key.columns,
        referenced_columns=foreign_key.referenced_columns,
        position=position,
        value=field_contract.value,
    )
    return FieldContract(
        name=field_contract.name,
        required=field_contract.required,
        nullable=field_contract.nullable,
        value=reference,
    )


def _apply_to_contract(
    contract: TypeContract,
    foreign_keys: Sequence[ForeignKeyConstraint],
) -> TypeContract:
    positions: dict[str, tuple[ForeignKeyConstraint, int]] = {}
    for foreign_key in foreign_keys:
        for position, column in enumerate(foreign_key.columns):
            positions[column] = (foreign_key, position)

    declared = set(positions)
    present = {field_contract.name for field_contract in contract.root.fields}
    missing = sorted(declared - present)
    if missing:
        names = ", ".join(f"{contract.concept_type}.{column}" for column in missing)
        message = (
            f"declared foreign key column is absent from every {contract.concept_type} "
            f"document: {names}"
        )
        _fail(message)

    fields = tuple(
        _referenced_field(field_contract, *positions[field_contract.name])
        if field_contract.name in positions
        else field_contract
        for field_contract in contract.root.fields
    )
    return TypeContract(
        concept_type=contract.concept_type,
        model_name=contract.model_name,
        root=ObjectNode(fields),
    )


def apply_references(
    contracts: Sequence[TypeContract],
    foreign_keys: Sequence[ForeignKeyConstraint],
) -> tuple[TypeContract, ...]:
    """Return the contracts with every declared foreign key compiled to a reference.

    A foreign key whose own type has no documents in the bundle contributes
    nothing: there is no contract to carry the reference, and an empty type is
    a valid relational target under RFC 0007.
    """
    by_table: dict[str, list[ForeignKeyConstraint]] = {}
    for foreign_key in sorted(foreign_keys, key=lambda item: (item.table, item.name)):
        by_table.setdefault(foreign_key.table, []).append(foreign_key)
    return tuple(
        _apply_to_contract(contract, by_table[contract.concept_type])
        if contract.concept_type in by_table
        else contract
        for contract in contracts
    )
