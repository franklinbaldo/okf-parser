"""Read bundle-level relational identity declared by trusted DuckDB SQL.

The binary runs the SQL and reads the keys back (``okf-db/src/relational.rs``);
``check --relational-schema`` validates a bundle against them natively. The
schema exporters read the same keys through :func:`load_relational_schema`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from okf_parser.rust_core import native_result

if TYPE_CHECKING:
    from pathlib import Path


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


class _RelationalSchemaRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    sql: str


class _Key(BaseModel):
    table: str
    columns: tuple[str, ...]
    name: str
    primary: bool


class _ForeignKey(BaseModel):
    table: str
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    name: str


class _Relational(BaseModel):
    keys: tuple[_Key, ...]
    foreign_keys: tuple[_ForeignKey, ...]


def parse_relational_schema(sql_text: str) -> RelationalSchema:
    """Execute trusted SQL and read PK/UNIQUE/FK metadata from DuckDB's catalog."""
    result = native_result(
        "__relational-schema",
        _RelationalSchemaRequest(sql=sql_text),
        {"relational_schema": RelationalSchemaError},
    )
    schema = _Relational.model_validate(result)
    return RelationalSchema(
        keys=tuple(KeyConstraint(**key.model_dump()) for key in schema.keys),
        foreign_keys=tuple(
            ForeignKeyConstraint(**foreign_key.model_dump()) for foreign_key in schema.foreign_keys
        ),
    )


def load_relational_schema(path: Path) -> RelationalSchema:
    """Read and execute one explicitly trusted bundle relational schema."""
    try:
        sql_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        message = f"could not read relational schema {path}: {exc}"
        raise RelationalSchemaError(message) from exc
    return parse_relational_schema(sql_text)


__all__ = [
    "ForeignKeyConstraint",
    "KeyConstraint",
    "RelationalSchema",
    "RelationalSchemaError",
    "load_relational_schema",
    "parse_relational_schema",
]
