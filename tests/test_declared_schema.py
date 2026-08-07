"""Tests for RFC 0006's declared-column-types SQL reader.

`.schema.sql` is trusted DuckDB SQL run whole, checked only by its
post-condition (exactly one non-temporary table named for the concept
type). These tests deliberately include CTAS, joins, and staging tables to
demonstrate the script is not restricted to plain `CREATE TABLE` DDL.
"""

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
    schema = parse_declared_schema(sql, "Rotina")

    assert schema.table_name == "Rotina"
    assert schema.table_comment == "Rotina administrativa."
    assert schema.column_comments == {"registrado_em": "Momento do registro."}
    assert set(schema.columns) == {"id", "registrado_em", "custo"}


def test_parse_declared_schema_handles_a_quoted_table_name_containing_whitespace() -> None:
    sql = 'CREATE TABLE "Blog Post" (date TIMESTAMPTZ, featured BOOLEAN);'
    schema = parse_declared_schema(sql, "Blog Post")

    assert schema.table_name == "Blog Post"
    assert set(schema.columns) == {"date", "featured"}


def test_parse_declared_schema_accepts_create_table_as_select() -> None:
    sql = """
    CREATE TABLE origem (id VARCHAR, custo VARCHAR);
    INSERT INTO origem VALUES ('r1', '10.50'), ('r2', '7');

    CREATE TABLE "Rotina" AS
    SELECT id, TRY_CAST(custo AS DECIMAL(18, 4)) AS custo
    FROM origem;
    """
    schema = parse_declared_schema(sql, "Rotina")

    assert schema.table_name == "Rotina"
    assert set(schema.columns) == {"id", "custo"}


def test_parse_declared_schema_ignores_auxiliary_and_temp_tables() -> None:
    sql = """
    CREATE TEMP TABLE staging (id VARCHAR);
    CREATE TABLE outra_coisa (id VARCHAR);
    CREATE TABLE "Rotina" (id VARCHAR);
    """
    schema = parse_declared_schema(sql, "Rotina")

    assert schema.table_name == "Rotina"
    assert set(schema.columns) == {"id"}


def test_parse_declared_schema_supports_joins_and_macros() -> None:
    sql = """
    CREATE MACRO dobro(x) AS x * 2;
    CREATE TABLE precos (id VARCHAR, valor INTEGER);
    INSERT INTO precos VALUES ('p1', 10);
    CREATE TABLE categorias (id VARCHAR, nome VARCHAR);
    INSERT INTO categorias VALUES ('p1', 'Bebidas');

    CREATE TABLE "Produto" AS
    SELECT precos.id, dobro(precos.valor) AS valor_dobrado, categorias.nome
    FROM precos JOIN categorias USING (id);
    """
    schema = parse_declared_schema(sql, "Produto")

    assert set(schema.columns) == {"id", "valor_dobrado", "nome"}


def test_parse_declared_schema_rejects_a_script_that_names_no_such_table() -> None:
    sql = 'CREATE TABLE "Outro" (id VARCHAR);'
    with pytest.raises(DeclaredSchemaError, match="did not leave behind"):
        parse_declared_schema(sql, "Rotina")


def test_parse_declared_schema_rejects_a_script_that_fails() -> None:
    with pytest.raises(DeclaredSchemaError, match="declared schema script failed"):
        parse_declared_schema("not sql at all (((", "Rotina")


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
    schema = parse_declared_schema(sql, "Rotina")
    kinds = declared_cast_kinds(schema)

    assert kinds == {
        "nome": "string",
        "ativo": "boolean",
        "idade": "integer",
        "custo": "number",
        "nascimento": "date",
        "registrado_em": "datetime",
    }
