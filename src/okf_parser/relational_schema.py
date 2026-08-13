"""Validate bundle-level relational identity declared by trusted DuckDB SQL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import duckdb

from okf_parser.models import Severity, Violation
from okf_parser.typed_tables import duckdb_identifier_key

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from okf_parser.bundle import Bundle
    from okf_parser.models import YamlValue


class RelationalSchemaError(ValueError):
    """Raised when a bundle relational schema cannot be executed or inspected."""


@dataclass(frozen=True, slots=True)
class KeyConstraint:
    """One PRIMARY KEY or UNIQUE constraint over a concept type."""

    table: str
    columns: tuple[str, ...]
    name: str
    primary: bool


@dataclass(frozen=True, slots=True)
class ForeignKeyConstraint:
    """One ordered foreign-key mapping between two concept types."""

    table: str
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    name: str


@dataclass(frozen=True, slots=True)
class RelationalSchema:
    """The recognized relational subset read back from DuckDB's catalog."""

    keys: tuple[KeyConstraint, ...]
    foreign_keys: tuple[ForeignKeyConstraint, ...]


@dataclass(frozen=True, slots=True)
class _Concept:
    path: str
    frontmatter: dict[str, YamlValue]


_INVALID = object()


def parse_relational_schema(sql_text: str) -> RelationalSchema:
    """Execute trusted SQL and read PK/UNIQUE/FK metadata from DuckDB's catalog."""
    con = duckdb.connect()
    try:
        try:
            con.execute(sql_text)
        except duckdb.Error as exc:
            message = f"relational schema script failed: {exc}"
            raise RelationalSchemaError(message) from exc

        table_rows = con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE NOT temporary ORDER BY table_name"
        ).fetchall()
        tables = {str(row[0]) for row in table_rows}
        constraint_rows = con.execute(
            "SELECT table_name, constraint_type, constraint_column_names, constraint_name, "
            "referenced_table, referenced_column_names "
            "FROM duckdb_constraints() ORDER BY table_name, constraint_index"
        ).fetchall()
    finally:
        con.close()

    keys: list[KeyConstraint] = []
    foreign_keys: list[ForeignKeyConstraint] = []
    for row in constraint_rows:
        table = str(row[0])
        if table not in tables:
            continue
        kind = str(row[1])
        columns = tuple(str(value) for value in (row[2] or ()))
        name = str(row[3] or f"{table}_{kind.lower().replace(' ', '_')}")
        if kind in {"PRIMARY KEY", "UNIQUE"}:
            keys.append(
                KeyConstraint(
                    table=table,
                    columns=columns,
                    name=name,
                    primary=kind == "PRIMARY KEY",
                )
            )
            continue
        if kind != "FOREIGN KEY":
            continue
        referenced_table = row[4]
        referenced_columns = row[5]
        if referenced_table is None or referenced_columns is None:
            message = f"foreign key {name!r} has incomplete catalog metadata"
            raise RelationalSchemaError(message)
        foreign_keys.append(
            ForeignKeyConstraint(
                table=table,
                columns=columns,
                referenced_table=str(referenced_table),
                referenced_columns=tuple(str(value) for value in referenced_columns),
                name=name,
            )
        )

    return RelationalSchema(keys=tuple(keys), foreign_keys=tuple(foreign_keys))


def load_relational_schema(path: Path) -> RelationalSchema:
    """Read and execute one explicitly trusted bundle relational schema."""
    try:
        sql_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        message = f"could not read relational schema {path}: {exc}"
        raise RelationalSchemaError(message) from exc
    return parse_relational_schema(sql_text)


def _concepts_by_type(bundle: Bundle) -> dict[str, list[_Concept]]:
    rows: dict[str, list[_Concept]] = {}
    for record in bundle.concepts.execute().to_dict(orient="records"):
        raw = record.get("frontmatter_json")
        if not isinstance(raw, str):
            continue
        frontmatter = json.loads(raw)
        if not isinstance(frontmatter, dict):
            continue
        concept_type = str(record.get("concept_type") or "")
        rows.setdefault(concept_type, []).append(
            _Concept(path=str(record.get("path") or ""), frontmatter=frontmatter)
        )
    return rows


def _field_value(
    concept: _Concept,
    field: str,
    *,
    constraint: str,
    diagnostics: list[Violation],
) -> str | None | object:
    wanted = duckdb_identifier_key(field)
    matches = [name for name in concept.frontmatter if duckdb_identifier_key(name) == wanted]
    if not matches:
        return None
    if len(matches) > 1:
        names = ", ".join(repr(name) for name in sorted(matches))
        diagnostics.append(
            Violation(
                code="OKF020",
                severity=Severity.ERROR,
                path=concept.path,
                message=(
                    f"relational constraint {constraint!r} cannot resolve column {field!r}: "
                    f"frontmatter keys collide under DuckDB identifier equality: {names}"
                ),
            )
        )
        return _INVALID
    value = concept.frontmatter[matches[0]]
    if isinstance(value, str) or value is None:
        return value
    diagnostics.append(
        Violation(
            code="OKF020",
            severity=Severity.ERROR,
            path=concept.path,
            message=(
                f"relational constraint {constraint!r} requires scalar string column "
                f"{field!r}; found {type(value).__name__}"
            ),
        )
    )
    return _INVALID


def _key_tuple(
    concept: _Concept,
    columns: Sequence[str],
    *,
    constraint: str,
    diagnostics: list[Violation],
) -> tuple[str | None, ...] | None:
    values = tuple(
        _field_value(concept, column, constraint=constraint, diagnostics=diagnostics)
        for column in columns
    )
    if any(value is _INVALID for value in values):
        return None
    return tuple(value if isinstance(value, str) else None for value in values)


def _validate_keys(
    concepts: Mapping[str, Sequence[_Concept]],
    schema: RelationalSchema,
    diagnostics: list[Violation],
) -> None:
    for constraint in schema.keys:
        seen: dict[tuple[str, ...], str] = {}
        for concept in concepts.get(constraint.table, ()):
            key = _key_tuple(
                concept,
                constraint.columns,
                constraint=constraint.name,
                diagnostics=diagnostics,
            )
            if key is None:
                continue
            if any(value is None for value in key):
                if constraint.primary:
                    diagnostics.append(
                        Violation(
                            code="OKF021",
                            severity=Severity.ERROR,
                            path=concept.path,
                            message=(
                                f"primary key {constraint.name!r} on {constraint.table!r} "
                                f"requires {constraint.columns!r} to be present"
                            ),
                        )
                    )
                continue
            concrete = tuple(value for value in key if value is not None)
            previous = seen.get(concrete)
            if previous is None:
                seen[concrete] = concept.path
                continue
            diagnostics.append(
                Violation(
                    code="OKF021",
                    severity=Severity.ERROR,
                    path=concept.path,
                    message=(
                        f"constraint {constraint.name!r} on {constraint.table!r} duplicates "
                        f"key {concrete!r}; first seen at {previous}"
                    ),
                )
            )


def _referenced_values(
    concepts: Mapping[str, Sequence[_Concept]],
    constraint: ForeignKeyConstraint,
    diagnostics: list[Violation],
) -> set[tuple[str, ...]]:
    values: set[tuple[str, ...]] = set()
    for concept in concepts.get(constraint.referenced_table, ()):
        key = _key_tuple(
            concept,
            constraint.referenced_columns,
            constraint=constraint.name,
            diagnostics=diagnostics,
        )
        if key is None or any(value is None for value in key):
            continue
        values.add(tuple(value for value in key if value is not None))
    return values


def _validate_foreign_keys(
    concepts: Mapping[str, Sequence[_Concept]],
    schema: RelationalSchema,
    diagnostics: list[Violation],
) -> None:
    for constraint in schema.foreign_keys:
        target_values = _referenced_values(concepts, constraint, diagnostics)
        for concept in concepts.get(constraint.table, ()):
            key = _key_tuple(
                concept,
                constraint.columns,
                constraint=constraint.name,
                diagnostics=diagnostics,
            )
            if key is None or any(value is None for value in key):
                continue
            concrete = tuple(value for value in key if value is not None)
            if concrete in target_values:
                continue
            diagnostics.append(
                Violation(
                    code="OKF022",
                    severity=Severity.ERROR,
                    path=concept.path,
                    message=(
                        f"foreign key {constraint.name!r} on {constraint.table!r} has no "
                        f"matching {constraint.referenced_table!r}"
                        f"{constraint.referenced_columns!r} for {concrete!r}"
                    ),
                )
            )


def validate_relations(bundle: Bundle, schema_path: Path) -> list[Violation]:
    """Validate bundle frontmatter against one explicitly trusted relational schema."""
    schema = load_relational_schema(schema_path)
    concepts = _concepts_by_type(bundle)
    diagnostics: list[Violation] = []
    _validate_keys(concepts, schema, diagnostics)
    _validate_foreign_keys(concepts, schema, diagnostics)
    return diagnostics


__all__ = [
    "ForeignKeyConstraint",
    "KeyConstraint",
    "RelationalSchema",
    "RelationalSchemaError",
    "load_relational_schema",
    "parse_relational_schema",
    "validate_relations",
]
