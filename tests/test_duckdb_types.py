"""Unit tests for the lossless DuckDB logical-type IR."""

from okf_parser.duckdb_types import logical_type_from_catalog


def test_catalog_type_ir_keeps_decimal_parameters_and_unknown_types() -> None:
    decimal = logical_type_from_catalog("DECIMAL(18,4)", numeric_precision=18, numeric_scale=4)
    future = logical_type_from_catalog("STRUCT(a INTEGER)")

    assert decimal.family == "decimal"
    assert decimal.precision == 18
    assert decimal.scale == 4
    assert future.family == "unsupported"
    assert future.sql == "STRUCT(a INTEGER)"


def test_catalog_type_ir_recurses_into_lists() -> None:
    logical = logical_type_from_catalog("UUID[]")

    assert logical.family == "list"
    assert logical.element is not None
    assert logical.element.family == "uuid"
