"""Bundle-level key and reference contracts across declared OKF types."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from okf_parser.models import Severity, Violation

if TYPE_CHECKING:
    from collections.abc import Callable

    from ibis.expr.types import Table

CONTRACT_FILENAME = "okf.relations.toml"


class RelationalContractError(ValueError):
    """Raised when ``okf.relations.toml`` is structurally invalid."""


@dataclass(frozen=True, slots=True)
class KeyContract:
    """A non-null unique key on one declared type."""

    concept_type: str
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReferenceContract:
    """A same-arity N:1 reference from one declared type to another key."""

    from_type: str
    from_fields: tuple[str, ...]
    to_type: str
    to_fields: tuple[str, ...]
    nullable: bool = False


@dataclass(frozen=True, slots=True)
class RelationalContract:
    """All bundle-level relational invariants."""

    keys: tuple[KeyContract, ...]
    references: tuple[ReferenceContract, ...]


def _nonempty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RelationalContractError(f"{label} must be a non-empty string")
    return value


def _fields(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RelationalContractError(f"{label} must be a non-empty array of field names")
    result = tuple(_nonempty_text(item, label) for item in value)
    if len(set(result)) != len(result):
        raise RelationalContractError(f"{label} contains duplicate field names")
    return result


def parse_relational_contract(text: str) -> RelationalContract:
    """Parse the small TOML contract language without inventing domain semantics."""
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RelationalContractError(f"invalid {CONTRACT_FILENAME}: {exc}") from exc

    allowed = {"key", "reference"}
    unknown = set(payload) - allowed
    if unknown:
        raise RelationalContractError(
            f"unknown top-level keys in {CONTRACT_FILENAME}: {', '.join(sorted(unknown))}"
        )

    keys_raw = payload.get("key", [])
    refs_raw = payload.get("reference", [])
    if not isinstance(keys_raw, list) or not all(isinstance(item, dict) for item in keys_raw):
        raise RelationalContractError("[[key]] entries must be TOML tables")
    if not isinstance(refs_raw, list) or not all(isinstance(item, dict) for item in refs_raw):
        raise RelationalContractError("[[reference]] entries must be TOML tables")

    keys: list[KeyContract] = []
    for index, item in enumerate(keys_raw):
        assert isinstance(item, dict)
        unknown_item = set(item) - {"type", "fields"}
        if unknown_item:
            raise RelationalContractError(
                f"key[{index}] has unknown fields: {', '.join(sorted(unknown_item))}"
            )
        keys.append(
            KeyContract(
                concept_type=_nonempty_text(item.get("type"), f"key[{index}].type"),
                fields=_fields(item.get("fields"), f"key[{index}].fields"),
            )
        )

    references: list[ReferenceContract] = []
    for index, item in enumerate(refs_raw):
        assert isinstance(item, dict)
        unknown_item = set(item) - {
            "from_type",
            "from_fields",
            "to_type",
            "to_fields",
            "nullable",
        }
        if unknown_item:
            raise RelationalContractError(
                f"reference[{index}] has unknown fields: {', '.join(sorted(unknown_item))}"
            )
        from_fields = _fields(item.get("from_fields"), f"reference[{index}].from_fields")
        to_fields = _fields(item.get("to_fields"), f"reference[{index}].to_fields")
        if len(from_fields) != len(to_fields):
            raise RelationalContractError(
                f"reference[{index}] source and target fields must have the same arity"
            )
        nullable = item.get("nullable", False)
        if not isinstance(nullable, bool):
            raise RelationalContractError(f"reference[{index}].nullable must be boolean")
        references.append(
            ReferenceContract(
                from_type=_nonempty_text(item.get("from_type"), f"reference[{index}].from_type"),
                from_fields=from_fields,
                to_type=_nonempty_text(item.get("to_type"), f"reference[{index}].to_type"),
                to_fields=to_fields,
                nullable=nullable,
            )
        )

    key_identities = [(item.concept_type, item.fields) for item in keys]
    if len(set(key_identities)) != len(key_identities):
        raise RelationalContractError("duplicate [[key]] declarations")
    ref_identities = [
        (item.from_type, item.from_fields, item.to_type, item.to_fields)
        for item in references
    ]
    if len(set(ref_identities)) != len(ref_identities):
        raise RelationalContractError("duplicate [[reference]] declarations")

    declared_keys = set(key_identities)
    for index, ref in enumerate(references):
        if (ref.to_type, ref.to_fields) not in declared_keys:
            raise RelationalContractError(
                f"reference[{index}] target {ref.to_type}.{ref.to_fields!r} is not a declared [[key]]"
            )

    return RelationalContract(keys=tuple(keys), references=tuple(references))


def load_relational_contract(root: str | Path) -> RelationalContract | None:
    """Load the optional bundle-level relational contract."""
    path = Path(root) / CONTRACT_FILENAME
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RelationalContractError(f"could not read {CONTRACT_FILENAME}: {exc}") from exc
    return parse_relational_contract(text)


def _missing_columns(table: Table, fields: tuple[str, ...]) -> tuple[str, ...]:
    names = set(table.schema().names)
    return tuple(field for field in fields if field not in names)


def _error(code: str, message: str) -> Violation:
    return Violation(
        code=code,
        severity=Severity.ERROR,
        path=CONTRACT_FILENAME,
        message=message,
    )


def validate_relational_contract(
    contract: RelationalContract,
    *,
    relation: Callable[[str], Table],
    available_types: set[str],
) -> tuple[Violation, ...]:
    """Validate keys and N:1 references with deterministic relational queries."""
    diagnostics: list[Violation] = []

    for key in contract.keys:
        if key.concept_type not in available_types:
            diagnostics.append(
                _error("relational-missing-type", f"key type {key.concept_type!r} is not materialized")
            )
            continue
        table = relation(key.concept_type)
        missing = _missing_columns(table, key.fields)
        if missing:
            diagnostics.append(
                _error(
                    "relational-missing-field",
                    f"key {key.concept_type}.{key.fields!r} references missing fields {missing!r}",
                )
            )
            continue
        null_predicate = None
        for field in key.fields:
            predicate = table[field].isnull()
            null_predicate = predicate if null_predicate is None else (null_predicate | predicate)
        assert null_predicate is not None
        null_count = table.filter(null_predicate).count().execute()
        if int(null_count) > 0:
            diagnostics.append(
                _error(
                    "relational-null-key",
                    f"key {key.concept_type}.{key.fields!r} has {int(null_count)} row(s) with null components",
                )
            )
        duplicate_count = (
            table.group_by(list(key.fields))
            .aggregate(__okf_count=table.count())
            .filter(lambda t: t.__okf_count > 1)
            .count()
            .execute()
        )
        if int(duplicate_count) > 0:
            diagnostics.append(
                _error(
                    "relational-duplicate-key",
                    f"key {key.concept_type}.{key.fields!r} has {int(duplicate_count)} duplicated value group(s)",
                )
            )

    for ref in contract.references:
        missing_types = [
            concept_type
            for concept_type in (ref.from_type, ref.to_type)
            if concept_type not in available_types
        ]
        if missing_types:
            diagnostics.append(
                _error(
                    "relational-missing-type",
                    f"reference {ref.from_type}.{ref.from_fields!r} -> {ref.to_type}.{ref.to_fields!r} uses missing type(s) {tuple(missing_types)!r}",
                )
            )
            continue
        source = relation(ref.from_type)
        target = relation(ref.to_type)
        missing_source = _missing_columns(source, ref.from_fields)
        missing_target = _missing_columns(target, ref.to_fields)
        if missing_source or missing_target:
            diagnostics.append(
                _error(
                    "relational-missing-field",
                    f"reference {ref.from_type}.{ref.from_fields!r} -> {ref.to_type}.{ref.to_fields!r} has missing source {missing_source!r} target {missing_target!r}",
                )
            )
            continue

        any_null = None
        all_null = None
        for field in ref.from_fields:
            predicate = source[field].isnull()
            any_null = predicate if any_null is None else (any_null | predicate)
            all_null = predicate if all_null is None else (all_null & predicate)
        assert any_null is not None and all_null is not None
        bad_nulls = any_null if not ref.nullable else (any_null & ~all_null)
        bad_null_count = source.filter(bad_nulls).count().execute()
        if int(bad_null_count) > 0:
            diagnostics.append(
                _error(
                    "relational-null-reference",
                    f"reference {ref.from_type}.{ref.from_fields!r} has {int(bad_null_count)} row(s) with invalid null components",
                )
            )

        source_non_null = source.filter(~any_null).select(list(ref.from_fields)).distinct()
        target_keys = target.select(list(ref.to_fields)).distinct()
        rename_target = target_keys.rename(
            {source_name: target_name for source_name, target_name in zip(ref.from_fields, ref.to_fields, strict=True)}
        )
        orphan_count = source_non_null.anti_join(
            rename_target,
            predicates=[source_non_null[field] == rename_target[field] for field in ref.from_fields],
        ).count().execute()
        if int(orphan_count) > 0:
            diagnostics.append(
                _error(
                    "relational-orphan-reference",
                    f"reference {ref.from_type}.{ref.from_fields!r} -> {ref.to_type}.{ref.to_fields!r} has {int(orphan_count)} orphan value(s)",
                )
            )

    return tuple(sorted(diagnostics, key=lambda item: (item.code, item.message)))
