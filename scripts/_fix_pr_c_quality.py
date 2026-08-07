from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace(
    "src/okf_parser/typed_tables.py",
    '''    from collections.abc import Mapping, Sequence
''',
    '''    from collections.abc import Iterable, Mapping, Sequence
''',
)
replace(
    "src/okf_parser/typed_tables.py",
    '''    concept_types: Sequence[str],
    spec_template: str | None,
''',
    '''    concept_types: Iterable[str],
    spec_template: str | None,
''',
)
replace(
    "src/okf_parser/typed_tables.py",
    '''    def __init__(self, schema_name: str, tables: tuple[str, ...]) -> None:
        self.schema_name = schema_name
''',
    '''    def __init__(self, schema_name: str, tables: tuple[str, ...]) -> None:
        """Record the typed schema and tables that would be replaced."""
        self.schema_name = schema_name
''',
)
replace(
    "src/okf_parser/typed_tables.py",
    '''        "SELECT table_name FROM information_schema.tables WHERE table_schema = ? ORDER BY table_name",
''',
    '''        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = ? ORDER BY table_name",
''',
)
replace(
    "src/okf_parser/typed_tables.py",
    '''        assert field.raw_name is not None
        assert field.raw_sql_type is not None
        assert field.declared_type is not None
        columns.append(f"{_quote_ident(field.raw_name)} {field.raw_sql_type}")
        columns.append(
            f"{_quote_ident(field.name)} {field.declared_type.sql} GENERATED ALWAYS AS "
            f"(TRY_CAST({_quote_ident(field.raw_name)} AS {field.declared_type.sql}))"
        )
''',
    '''        raw_name = field.raw_name
        raw_sql_type = field.raw_sql_type
        declared_type = field.declared_type
        if raw_name is None or raw_sql_type is None or declared_type is None:
            message = f"declared field {field.name!r} has an incomplete typed-table plan"
            raise TypedTableError(message)
        columns.append(f"{_quote_ident(raw_name)} {raw_sql_type}")
        columns.append(
            f"{_quote_ident(field.name)} {declared_type.sql} GENERATED ALWAYS AS "
            f"(TRY_CAST({_quote_ident(raw_name)} AS {declared_type.sql}))"
        )
''',
)
replace(
    "src/okf_parser/typed_tables.py",
    '''            field.name: cast("DuckDBLogicalType", field.declared_type)
            for field in plan.fields
            if field.declared_type is not None
''',
    '''            field.name: field.declared_type
            for field in plan.fields
            if field.declared_type is not None
''',
)
replace(
    "src/okf_parser/typed_tables.py",
    '''def _insert_columns(plan: TypedTablePlan) -> tuple[str, ...]:
    columns = list(_INTERNAL_COLUMNS)
    for field in plan.fields:
        columns.append(field.raw_name if field.declared else field.name)
    return tuple(cast("str", name) for name in columns)
''',
    '''def _insert_name(field: TypedFieldPlan) -> str:
    if not field.declared:
        return field.name
    if field.raw_name is None:
        message = f"declared field {field.name!r} has no raw column"
        raise TypedTableError(message)
    return field.raw_name


def _insert_columns(plan: TypedTablePlan) -> tuple[str, ...]:
    columns = list(_INTERNAL_COLUMNS)
    columns.extend(_insert_name(field) for field in plan.fields)
    return tuple(columns)
''',
)
replace(
    "pyproject.toml",
    '''"src/okf_parser/declared_schema.py" = ["S608"]
"src/okf_parser/cli.py" = ["A002", "PLR0913", "T201"]
''',
    '''"src/okf_parser/declared_schema.py" = ["S608"]
"src/okf_parser/duckdb.py" = ["PLR0913"]
"src/okf_parser/typed_tables.py" = ["S608"]
"src/okf_parser/cli.py" = ["A002", "PLR0913", "T201"]
''',
)
replace(
    "tests/test_duckdb.py",
    '''    assert connection.execute('DESCRIBE okf_types."Rotina"').fetchone()[0] == "manual"
''',
    '''    description = connection.execute('DESCRIBE okf_types."Rotina"').fetchone()
    assert description is not None
    assert description[0] == "manual"
''',
)
