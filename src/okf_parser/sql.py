"""Query a loaded bundle with SQL: ``Bundle.sql()`` (RFC 0024 phase 4).

The binary materializes the bundle the caller holds into a fresh in-memory
DuckDB (``concepts``, ``links``, ``reserved``, ``diagnostics`` and, with a
spec template, one table per declared type), locks external access, and runs
one read-only query. Values cross as JSON, text where a JSON number would
lose precision or meaning, and are read back here by each column's DuckDB
type: ``DECIMAL`` as :class:`~decimal.Decimal`, temporal types as
:mod:`datetime` values, ``BLOB`` as bytes, ``JSON`` parsed, lists, structs
and maps recursively.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, JsonValue

from okf_parser.declared_schema import DeclaredSchemaError
from okf_parser.rust_core import native_result
from okf_parser.type_specs import SpecTemplateError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from okf_parser.bundle import Bundle


class SqlError(ValueError):
    """The query is not valid SQL, or not one read-only query."""


@dataclass(frozen=True, slots=True)
class SqlColumn:
    """One result column: its name and DuckDB type, as ``DESCRIBE`` spells it."""

    name: str
    type: str


@dataclass(frozen=True, slots=True)
class SqlResult:
    """The rows of one query, in the order the query produced them."""

    columns: tuple[SqlColumn, ...]
    rows: tuple[tuple[object, ...], ...]
    truncated: bool = False
    """Whether ``limit`` cut the rows short."""

    @property
    def column_names(self) -> tuple[str, ...]:
        """The result's column names, in order."""
        return tuple(column.name for column in self.columns)

    def to_dicts(self) -> list[dict[str, object]]:
        """Every row as a mapping of column name to value."""
        names = self.column_names
        return [dict(zip(names, row, strict=True)) for row in self.rows]

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        """Iterate the rows."""
        return iter(self.rows)

    def __len__(self) -> int:
        """The number of rows."""
        return len(self.rows)


def _split_top_level(text: str) -> list[str]:
    """Split ``text`` at commas outside parentheses, brackets and quotes."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    for index, character in enumerate(text):
        if quote is not None:
            if character == quote:
                quote = None
        elif character in "\"'":
            quote = character
        elif character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _struct_field(spec: str) -> tuple[str, str]:
    """``name TYPE`` or ``"quoted name" TYPE`` of one struct field."""
    if spec.startswith('"'):
        end = spec.index('"', 1)
        while spec[end + 1 : end + 2] == '"':
            end = spec.index('"', end + 2)
        return spec[1:end].replace('""', '"'), spec[end + 1 :].strip()
    name, _, field_type = spec.partition(" ")
    return name, field_type.strip()


def _inner(sql_type: str) -> str:
    return sql_type[sql_type.index("(") + 1 : sql_type.rindex(")")]


def _decode_nested(value: JsonValue, sql_type: str, upper: str) -> object:
    if sql_type.endswith("]") and isinstance(value, list):
        element = sql_type[: sql_type.rindex("[")]
        return [decode_value(item, element) for item in value]
    if upper.startswith("STRUCT(") and isinstance(value, dict):
        fields = dict(_struct_field(spec) for spec in _split_top_level(_inner(sql_type)))
        return {
            name: decode_value(item, fields.get(name, "VARCHAR")) for name, item in value.items()
        }
    if upper.startswith("MAP(") and isinstance(value, list):
        key_type, value_type = _split_top_level(_inner(sql_type))
        pairs = [
            (decode_value(pair[0], key_type), decode_value(pair[1], value_type))
            for pair in value
            if isinstance(pair, list) and len(pair) == 2  # noqa: PLR2004 - a key and a value.
        ]
        try:
            return dict(pairs)
        except TypeError:
            return pairs
    return value


_TEXT_DECODERS: dict[str, Callable[[str], object]] = {
    "HUGEINT": int,
    "UHUGEINT": int,
    "FLOAT": float,
    "REAL": float,
    "DOUBLE": float,
    "UUID": uuid.UUID,
    "BLOB": bytes.fromhex,
    "JSON": json.loads,
    "DATE": date.fromisoformat,
    "TIME": time.fromisoformat,
}
"""Types whose JSON form is text, by exact name."""


def _decode_text(value: str, upper: str) -> object:
    decoder = _TEXT_DECODERS.get(upper)
    if decoder is not None:
        return _temporal(decoder, value) if upper in {"DATE", "TIME"} else decoder(value)
    if upper.startswith("DECIMAL"):
        return Decimal(value)
    if upper.startswith("TIMESTAMP"):
        moment = _temporal(datetime.fromisoformat, value)
        if isinstance(moment, datetime) and "TIME ZONE" in upper:
            return moment.astimezone(UTC)
        return moment
    return value


def _temporal[T](parse: Callable[[str], T], value: str) -> T | str:
    """``infinity`` and ``-infinity`` have no :mod:`datetime` form; keep them."""
    try:
        return parse(value)
    except ValueError:
        return value


def decode_value(value: JsonValue, sql_type: str) -> object:
    """Read one JSON-encoded DuckDB value back as the Python value its type means."""
    if value is None:
        return None
    sql_type = " ".join(sql_type.split())
    upper = sql_type.upper()
    if isinstance(value, (list, dict)):
        return _decode_nested(value, sql_type, upper)
    if isinstance(value, str):
        return _decode_text(value, upper)
    return value


class _SqlRequest(BaseModel):
    """The ``__sql`` request the binary validates."""

    model_config = ConfigDict(frozen=True)

    bundle: dict[str, JsonValue]
    query: str
    spec_template: str | None
    limit: int | None
    relations: bool


class _Column(BaseModel):
    name: str
    type: str


class _Result(BaseModel):
    columns: tuple[_Column, ...]
    rows: tuple[tuple[JsonValue, ...], ...]
    truncated: bool


def bundle_records(bundle: Bundle) -> Mapping[str, JsonValue]:
    """The records a `Bundle` holds, as the binary's requests carry them."""
    return {
        "root": str(bundle.root),
        "concepts": [record.model_dump(mode="json") for record in bundle.concepts],
        "reserved": [record.model_dump(mode="json") for record in bundle.reserved],
        "links": [record.model_dump(mode="json") for record in bundle.links],
        "diagnostics": [item.model_dump(mode="json") for item in bundle.diagnostics],
        "markdown_count": bundle.markdown_count,
    }


def query_bundle(
    bundle: Bundle,
    query: str,
    *,
    spec_template: str | None = None,
    limit: int | None = None,
    relations: bool = False,
) -> SqlResult:
    """Run one read-only query over ``bundle``; see :meth:`Bundle.sql`."""
    result = native_result(
        "__sql",
        _SqlRequest(
            bundle=dict(bundle_records(bundle)),
            query=query,
            spec_template=spec_template,
            limit=limit,
            relations=relations,
        ),
        {
            "query": SqlError,
            "spec_template": SpecTemplateError,
            "declared_schema": DeclaredSchemaError,
        },
    )
    answer = _Result.model_validate(result)
    columns = tuple(SqlColumn(column.name, column.type) for column in answer.columns)
    rows = tuple(
        tuple(decode_value(value, column.type) for value, column in zip(row, columns, strict=True))
        for row in answer.rows
    )
    return SqlResult(columns=columns, rows=rows, truncated=answer.truncated)
