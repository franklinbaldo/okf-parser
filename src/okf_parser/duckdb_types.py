"""Lossless logical-type IR for types read back from DuckDB's catalog.

DuckDB remains the parser and normalizer. This module only classifies the
normalized ``data_type`` returned by ``duckdb_columns()`` so exporters do not
collapse physical intent into the smaller observation ``CastKind`` vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from okf_parser.schema_lexemes import CastKind

type DuckDBTypeFamily = Literal[
    "string",
    "boolean",
    "integer",
    "float",
    "decimal",
    "date",
    "timestamp",
    "timestamptz",
    "uuid",
    "list",
    "unsupported",
]

_INTEGER_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
    }
)
_JS_SAFE_INTEGER_TYPES = frozenset(
    {"TINYINT", "SMALLINT", "INTEGER", "UTINYINT", "USMALLINT", "UINTEGER"}
)
_SCALAR_FAMILIES: dict[str, DuckDBTypeFamily] = {
    "VARCHAR": "string",
    "BOOLEAN": "boolean",
    **dict.fromkeys(_INTEGER_TYPES, "integer"),
    "FLOAT": "float",
    "REAL": "float",
    "DOUBLE": "float",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP_S": "timestamp",
    "TIMESTAMP_MS": "timestamp",
    "TIMESTAMP_NS": "timestamp",
    "TIMESTAMPTZ": "timestamptz",
    "TIMESTAMP WITH TIME ZONE": "timestamptz",
    "UUID": "uuid",
}
_DECIMAL_RE = re.compile(r"^DECIMAL\((?P<precision>\d+),\s*(?P<scale>\d+)\)$")


@dataclass(frozen=True, slots=True)
class DuckDBLogicalType:
    """One DuckDB catalog type without throwing away physical detail."""

    sql: str
    family: DuckDBTypeFamily
    precision: int | None = None
    scale: int | None = None
    element: DuckDBLogicalType | None = None

    @property
    def cast_kind(self) -> CastKind | None:
        """Return the legacy scalar vocabulary where it remains adequate."""
        kinds: dict[DuckDBTypeFamily, CastKind] = {
            "string": "string",
            "boolean": "boolean",
            "integer": "integer",
            "float": "number",
            "decimal": "number",
            "date": "date",
            "timestamp": "datetime",
            "timestamptz": "datetime",
            "uuid": "string",
        }
        return kinds.get(self.family)

    @property
    def is_js_safe_integer(self) -> bool:
        """Whether every value of this integral type fits ECMAScript's safe range."""
        return self.family == "integer" and self.sql.upper() in _JS_SAFE_INTEGER_TYPES


def _decimal_parameters(sql: str) -> tuple[int | None, int | None]:
    match = _DECIMAL_RE.fullmatch(sql.upper())
    if match is None:
        return None, None
    return int(match.group("precision")), int(match.group("scale"))


def logical_type_from_catalog(
    data_type: str,
    *,
    numeric_precision: int | None = None,
    numeric_scale: int | None = None,
) -> DuckDBLogicalType:
    """Classify a normalized catalog type while always preserving its SQL identity."""
    sql = " ".join(data_type.strip().split())
    key = sql.upper()
    if key.endswith("[]"):
        return DuckDBLogicalType(
            sql=sql,
            family="list",
            element=logical_type_from_catalog(sql[:-2]),
        )
    if key.startswith("DECIMAL("):
        parsed_precision, parsed_scale = _decimal_parameters(sql)
        return DuckDBLogicalType(
            sql,
            "decimal",
            precision=numeric_precision if numeric_precision is not None else parsed_precision,
            scale=numeric_scale if numeric_scale is not None else parsed_scale,
        )
    return DuckDBLogicalType(sql, _SCALAR_FAMILIES.get(key, "unsupported"))
