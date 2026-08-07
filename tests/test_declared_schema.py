"""Tests for RFC 0006's declared-column-types DDL reader."""

from __future__ import annotations

import pytest

from okf_parser.declared_schema import (
    DeclaredSchemaError,
    declared_cast_kinds,
    declared_schema_relative_path,
    parse_declared_schema,
)

TEMPLATE = "docs/types/{slug}.md"


def test_declared_schema_relative_path_swaps_the_spec_extension() -> None:
    assert declared_schema_relative_path(TEMPLATE, "Rotina") == "docs/types/rotina.schema.sql"


def test_declared_schema_relative_path_reports_a_type_without_a_slug() -> None:
    assert declared_schema_relative_path(TEMPLATE, "概念") is None


def test_parse_declared_schema_reads_columns_and_comments() -> None:
    sql = """
    CREATE TABLE "Rotina" (
        id VARCHAR,
        registrado_em TIMESTAMPTZ,
        custo DECIMAL(18, 4)
    );

    COMMENT ON TABLE "Rotina" IS 'Rotina administrativa.';
    COMMENT ON COLUMN "Rotina".registrado_em IS 'Momento do registro.';
    """
    schema = parse_declared_schema(sql)

    assert schema.table_name == "Rotina"
    assert schema.table_comment == "Rotina administrativa."
    assert schema.column_comments == {"registrado_em": "Momento do registro."}
    assert set(schema.columns) == {"id", "registrado_em", "custo"}


def test_parse_declared_schema_rejects_more_than_one_create_table() -> None:
    sql = 'CREATE TABLE "A" (id VARCHAR); CREATE TABLE "B" (id VARCHAR);'
    with pytest.raises(DeclaredSchemaError, match="exactly one CREATE TABLE"):
        parse_declared_schema(sql)


def test_parse_declared_schema_rejects_statements_other_than_create_and_comment() -> None:
    sql = 'CREATE TABLE "A" (id VARCHAR); SELECT 1;'
    with pytest.raises(DeclaredSchemaError, match="may only contain"):
        parse_declared_schema(sql)


def test_parse_declared_schema_rejects_unparseable_sql() -> None:
    with pytest.raises(DeclaredSchemaError, match="could not be parsed"):
        parse_declared_schema("not sql at all (((")


def test_declared_cast_kinds_maps_duckdb_types_to_the_shared_vocabulary() -> None:
    sql = """
    CREATE TABLE "Rotina" (
        nome VARCHAR,
        ativo BOOLEAN,
        idade INTEGER,
        custo DECIMAL(18, 4),
        nascimento DATE,
        registrado_em TIMESTAMPTZ
    );
    """
    schema = parse_declared_schema(sql)
    kinds = declared_cast_kinds(schema)

    assert kinds == {
        "nome": "string",
        "ativo": "boolean",
        "idade": "integer",
        "custo": "number",
        "nascimento": "date",
        "registrado_em": "datetime",
    }
