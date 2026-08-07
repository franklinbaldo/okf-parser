"""Plan and materialize RFC 0006 raw-plus-typed concept tables."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from okf_parser.declared_schema import (
    DeclaredSchema,
    DeclaredSchemaError,
    declared_schema_relative_path,
    parse_declared_schema,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    import duckdb

    from okf_parser.bundle import Bundle
    from okf_parser.duckdb_types import DuckDBLogicalType
    from okf_parser.models import YamlValue

_INTERNAL_COLUMNS = (
    "__okf_path",
    "__okf_concept_id",
    "__okf_logical_key",
    "__okf_body",
    "__okf_body_lines",
)
_OKF_PREFIX = "__okf_"


class TypedTableError(ValueError):
    """Raised when observed fields cannot form one deterministic type table."""


class TypedTableCollisionError(TypedTableError):
    """Raised when the persistent typed schema already owns a planned table name."""

    def __init__(self, schema_name: str, tables: tuple[str, ...]) -> None:
        """Record the typed schema and tables that would be replaced."""
        self.schema_name = schema_name
        self.tables = tables
        super().__init__(f"schema {schema_name!r} already contains {', '.join(tables)}")


@dataclass(frozen=True, slots=True)
class TypedFieldPlan:
    """One public field and, when declared, its compiler-owned raw source."""

    name: str
    declared_type: DuckDBLogicalType | None = None
    raw_name: str | None = None
    comment: str | None = None

    @property
    def declared(self) -> bool:
        """Whether the field came from `.schema.sql`."""
        return self.declared_type is not None

    @property
    def raw_sql_type(self) -> str | None:
        """Return the lossless raw carrier shape for a declared field."""
        if self.declared_type is None:
            return None
        return "VARCHAR[]" if self.declared_type.family == "list" else "VARCHAR"


@dataclass(frozen=True, slots=True)
class TypedTablePlan:
    """The DuckDB-independent field plan for one declared concept type."""

    concept_type: str
    fields: tuple[TypedFieldPlan, ...]
    table_comment: str | None = None


@dataclass(frozen=True, slots=True)
class TypedMaterialization:
    """Summary of the persistent declared-type surface."""

    schema: str
    tables: tuple[str, ...]
    unrecognized_tables: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ConceptRow:
    path: str
    concept_id: str
    logical_key: str
    body: str
    frontmatter: dict[str, YamlValue]


def _identifier_key(identifier: str) -> str:
    """Fold ASCII letters the same way DuckDB resolves quoted identifiers."""
    return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in identifier)


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_reserved(name: str) -> bool:
    return name[: len(_OKF_PREFIX)].lower() == _OKF_PREFIX


def discover_declared_schemas(
    root: str | Path,
    concept_types: Iterable[str],
    spec_template: str | None,
) -> dict[str, DeclaredSchema]:
    """Execute and read every declaration reachable from one spec template."""
    if spec_template is None:
        return {}

    types_by_path: dict[str, list[str]] = {}
    for concept_type in concept_types:
        relative = declared_schema_relative_path(spec_template, concept_type)
        if relative is not None:
            types_by_path.setdefault(relative, []).append(concept_type)

    root_path = Path(root)
    declarations: dict[str, DeclaredSchema] = {}
    for relative, owners in sorted(types_by_path.items()):
        schema_path = root_path / relative
        if not schema_path.is_file():
            continue
        if len(owners) > 1:
            names = ", ".join(repr(name) for name in sorted(owners))
            message = f"declared schema path collision at {relative!r}: {names}"
            raise DeclaredSchemaError(message)
        concept_type = owners[0]
        try:
            sql_text = schema_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            message = f"could not read declared schema {relative!r}: {exc}"
            raise DeclaredSchemaError(message) from exc
        declarations[concept_type] = parse_declared_schema(sql_text, concept_type)
    return declarations


def _rows_by_type(bundle: Bundle) -> dict[str, list[_ConceptRow]]:
    rows: dict[str, list[_ConceptRow]] = {}
    records = bundle.concepts.execute().to_dict(orient="records")
    for record in records:
        frontmatter_raw = record.get("frontmatter_json")
        if not isinstance(frontmatter_raw, str):
            continue
        frontmatter = json.loads(frontmatter_raw)
        if not isinstance(frontmatter, dict):
            continue
        concept_type = str(record.get("concept_type") or "")
        rows.setdefault(concept_type, []).append(
            _ConceptRow(
                path=str(record.get("path") or ""),
                concept_id=str(record.get("concept_id") or ""),
                logical_key=str(record.get("logical_key") or ""),
                body=str(record.get("body") or ""),
                frontmatter=cast("dict[str, YamlValue]", frontmatter),
            )
        )
    return rows


def _declared_aliases(
    frontmatter: Mapping[str, YamlValue], declared: Mapping[str, DuckDBLogicalType]
) -> dict[str, str]:
    declared_by_key = {_identifier_key(name): name for name in declared}
    aliases: dict[str, str] = {}
    for authored in frontmatter:
        canonical = declared_by_key.get(_identifier_key(authored))
        if canonical is None:
            continue
        previous = aliases.get(canonical)
        if previous is not None and previous != authored:
            message = (
                f"frontmatter contains fields {previous!r} and {authored!r} that collide "
                f"with declared column {canonical!r} under DuckDB identifier equality"
            )
            raise TypedTableError(message)
        aliases[canonical] = authored
    return aliases


def compile_typed_table_plan(
    concept_type: str,
    frontmatters: Sequence[Mapping[str, YamlValue]],
    declaration: DeclaredSchema,
) -> TypedTablePlan:
    """Compile declared fields plus observed undeclared scalar fields into one plan."""
    declared = {name: kind for name, kind in declaration.columns.items() if not _is_reserved(name)}
    declared_keys = {_identifier_key(name) for name in declared}

    undeclared_spellings: dict[str, set[str]] = {}
    undeclared_structured: set[str] = set()
    for frontmatter in frontmatters:
        _declared_aliases(frontmatter, declared)
        for name, value in frontmatter.items():
            key = _identifier_key(name)
            if key in declared_keys or _is_reserved(name):
                continue
            undeclared_spellings.setdefault(key, set()).add(name)
            if not (isinstance(value, str) or value is None):
                undeclared_structured.add(key)

    fields: list[TypedFieldPlan] = []
    for name, logical_type in declaration.columns.items():
        if _is_reserved(name):
            continue
        fields.append(
            TypedFieldPlan(
                name=name,
                declared_type=logical_type,
                raw_name=f"__okf_raw_{name}",
                comment=declaration.column_comments.get(name),
            )
        )

    for key, spellings in sorted(undeclared_spellings.items()):
        if key in undeclared_structured:
            continue
        if len(spellings) != 1:
            names = ", ".join(repr(name) for name in sorted(spellings))
            message = (
                f"type {concept_type!r} has undeclared fields that collide under "
                f"DuckDB identifier equality: {names}"
            )
            raise TypedTableError(message)
        fields.append(TypedFieldPlan(name=next(iter(spellings))))

    return TypedTablePlan(
        concept_type=concept_type,
        fields=tuple(fields),
        table_comment=declaration.table_comment,
    )


def build_typed_table_plans(
    bundle: Bundle, declarations: Mapping[str, DeclaredSchema]
) -> tuple[TypedTablePlan, ...]:
    """Build deterministic plans for every concept type that opted into a declaration."""
    rows = _rows_by_type(bundle)
    return tuple(
        compile_typed_table_plan(
            concept_type,
            [row.frontmatter for row in rows.get(concept_type, ())],
            declaration,
        )
        for concept_type, declaration in sorted(declarations.items())
    )


def _table_names(connection: duckdb.DuckDBPyConnection, schema: str) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = ? ORDER BY table_name",
        [schema],
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _matching_existing_table(existing: Sequence[str], name: str) -> str | None:
    wanted = _identifier_key(name)
    matches = [item for item in existing if _identifier_key(item) == wanted]
    if not matches:
        return None
    if len(matches) > 1:
        message = f"schema contains more than one table resolving as {name!r}: {matches}"
        raise TypedTableError(message)
    return matches[0]


def _existing_comments(
    connection: duckdb.DuckDBPyConnection, schema: str, table: str
) -> tuple[str | None, dict[str, str]]:
    table_row = connection.execute(
        "SELECT comment FROM duckdb_tables() WHERE schema_name = ? AND table_name = ?",
        [schema, table],
    ).fetchone()
    table_comment = None if table_row is None else cast("str | None", table_row[0])
    column_rows = connection.execute(
        "SELECT column_name, comment FROM duckdb_columns() "
        "WHERE schema_name = ? AND table_name = ? AND comment IS NOT NULL",
        [schema, table],
    ).fetchall()
    return table_comment, {str(row[0]): str(row[1]) for row in column_rows}


def _ddl(plan: TypedTablePlan, schema: str) -> str:
    columns = [
        '"__okf_path" VARCHAR',
        '"__okf_concept_id" VARCHAR',
        '"__okf_logical_key" VARCHAR',
        '"__okf_body" VARCHAR',
        '"__okf_body_lines" VARCHAR[]',
    ]
    for field in plan.fields:
        if not field.declared:
            columns.append(f"{_quote_ident(field.name)} VARCHAR")
            continue
        raw_name = field.raw_name
        raw_sql_type = field.raw_sql_type
        declared_type = field.declared_type
        if raw_name is None or raw_sql_type is None or declared_type is None:
            message = f"declared field {field.name!r} has an incomplete typed-table plan"
            raise TypedTableError(message)
        columns.append(f"{_quote_ident(raw_name)} {raw_sql_type}")
        columns.append(f"{_quote_ident(field.name)} {declared_type.sql}")
    body = ",\n    ".join(columns)
    return f"CREATE TABLE {_quote_ident(schema)}.{_quote_ident(plan.concept_type)} (\n    {body}\n)"


def _raw_value(value: YamlValue, logical_type: DuckDBLogicalType) -> object:
    if value is None:
        return None
    if logical_type.family == "list":
        if isinstance(value, list) and all(isinstance(item, str) or item is None for item in value):
            return value
        return None
    return value if isinstance(value, str) else None


def _row_values(row: _ConceptRow, plan: TypedTablePlan) -> tuple[object, ...]:
    values: list[object] = [
        row.path,
        row.concept_id,
        row.logical_key,
        row.body,
        row.body.splitlines(),
    ]
    aliases = _declared_aliases(
        row.frontmatter,
        {
            field.name: field.declared_type
            for field in plan.fields
            if field.declared_type is not None
        },
    )
    for field in plan.fields:
        if field.declared_type is not None:
            authored = aliases.get(field.name)
            value = row.frontmatter.get(authored) if authored is not None else None
            values.append(_raw_value(value, field.declared_type))
        else:
            values.append(row.frontmatter.get(field.name))
    return tuple(values)


def _insert_name(field: TypedFieldPlan) -> str:
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


def _populate_typed_columns(
    connection: duckdb.DuckDBPyConnection,
    schema: str,
    plan: TypedTablePlan,
) -> None:
    assignments: list[str] = []
    for field in plan.fields:
        declared_type = field.declared_type
        if declared_type is None:
            continue
        raw_name = _insert_name(field)
        assignments.append(
            f"{_quote_ident(field.name)} = "
            f"TRY_CAST({_quote_ident(raw_name)} AS {declared_type.sql})"
        )
    if not assignments:
        return

    timezone_row = connection.execute("SELECT current_setting('TimeZone')").fetchone()
    if timezone_row is None:
        message = "DuckDB did not expose the current TimeZone setting"
        raise TypedTableError(message)
    previous_timezone = str(timezone_row[0])
    connection.execute("SET TimeZone = 'UTC'")
    try:
        connection.execute(
            f"UPDATE {_quote_ident(schema)}.{_quote_ident(plan.concept_type)} "
            f"SET {', '.join(assignments)}"
        )
    finally:
        connection.execute(f"SET TimeZone = {_quote_literal(previous_timezone)}")


def _apply_comments(
    connection: duckdb.DuckDBPyConnection,
    schema: str,
    plan: TypedTablePlan,
    prior_table_comment: str | None,
    prior_column_comments: Mapping[str, str],
) -> None:
    table_comment = plan.table_comment if plan.table_comment is not None else prior_table_comment
    if table_comment is not None:
        connection.execute(
            f"COMMENT ON TABLE {_quote_ident(schema)}.{_quote_ident(plan.concept_type)} "
            f"IS {_quote_literal(table_comment)}"
        )
    for field in plan.fields:
        comment = (
            field.comment if field.comment is not None else prior_column_comments.get(field.name)
        )
        if comment is None:
            continue
        connection.execute(
            f"COMMENT ON COLUMN {_quote_ident(schema)}.{_quote_ident(plan.concept_type)}."
            f"{_quote_ident(field.name)} IS {_quote_literal(comment)}"
        )


def materialize_typed_tables(
    connection: duckdb.DuckDBPyConnection,
    bundle: Bundle,
    *,
    schema: str,
    declarations: Mapping[str, DeclaredSchema],
    overwrite: bool,
) -> TypedMaterialization:
    """Persist every declared type table inside the caller's current transaction."""
    plans = build_typed_table_plans(bundle, declarations)
    existing = _table_names(connection, schema)
    planned_keys: dict[str, str] = {}
    for plan in plans:
        key = _identifier_key(plan.concept_type)
        previous = planned_keys.get(key)
        if previous is not None and previous != plan.concept_type:
            message = (
                f"concept types {previous!r} and {plan.concept_type!r} collide under "
                "DuckDB identifier equality"
            )
            raise TypedTableError(message)
        planned_keys[key] = plan.concept_type

    collisions = tuple(
        match
        for plan in plans
        if (match := _matching_existing_table(existing, plan.concept_type)) is not None
    )
    if collisions and not overwrite:
        raise TypedTableCollisionError(schema, tuple(sorted(collisions)))

    unrecognized = tuple(name for name in existing if _identifier_key(name) not in planned_keys)
    rows = _rows_by_type(bundle)
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(schema)}")
    for plan in plans:
        existing_name = _matching_existing_table(existing, plan.concept_type)
        prior_table_comment: str | None = None
        prior_column_comments: dict[str, str] = {}
        if existing_name is not None:
            prior_table_comment, prior_column_comments = _existing_comments(
                connection, schema, existing_name
            )
            connection.execute(f"DROP TABLE {_quote_ident(schema)}.{_quote_ident(existing_name)}")
        connection.execute(_ddl(plan, schema))
        insert_columns = _insert_columns(plan)
        type_rows = rows.get(plan.concept_type, ())
        if type_rows:
            column_sql = ", ".join(_quote_ident(name) for name in insert_columns)
            placeholders = ", ".join("?" for _ in insert_columns)
            connection.executemany(
                f"INSERT INTO {_quote_ident(schema)}.{_quote_ident(plan.concept_type)} "
                f"({column_sql}) VALUES ({placeholders})",
                [_row_values(row, plan) for row in type_rows],
            )
        _populate_typed_columns(connection, schema, plan)
        _apply_comments(
            connection,
            schema,
            plan,
            prior_table_comment,
            prior_column_comments,
        )

    return TypedMaterialization(
        schema=schema,
        tables=tuple(plan.concept_type for plan in plans),
        unrecognized_tables=unrecognized,
    )
