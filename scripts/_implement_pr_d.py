from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Expose the two pieces of the shared planner the ephemeral apply consumer needs.
replace(
    "src/okf_parser/typed_tables.py",
    "def _identifier_key(identifier: str) -> str:\n",
    "def duckdb_identifier_key(identifier: str) -> str:\n",
)
text = Path("src/okf_parser/typed_tables.py").read_text(encoding="utf-8")
text = text.replace("_identifier_key(", "duckdb_identifier_key(")
Path("src/okf_parser/typed_tables.py").write_text(text, encoding="utf-8")
replace(
    "src/okf_parser/typed_tables.py",
    '''def _raw_value(value: YamlValue, logical_type: DuckDBLogicalType) -> object:
    if value is None:
        return None
    if logical_type.family == "list":
        if isinstance(value, list) and all(isinstance(item, str) or item is None for item in value):
            return value
        return None
    return value if isinstance(value, str) else None
''',
    '''def declared_raw_value(value: YamlValue, logical_type: DuckDBLogicalType) -> object:
    """Project one authored value into the lossless raw carrier for a declared type."""
    if value is None:
        return None
    if logical_type.family == "list":
        if isinstance(value, list) and all(isinstance(item, str) or item is None for item in value):
            return value
        return None
    return value if isinstance(value, str) else None


def field_input_value(
    frontmatter: Mapping[str, YamlValue], field: TypedFieldPlan
) -> object:
    """Return the insertable source value for one planned public field."""
    if field.declared_type is None:
        value = frontmatter.get(field.name)
        return value if isinstance(value, str) or value is None else None
    aliases = _declared_aliases(frontmatter, {field.name: field.declared_type})
    authored = aliases.get(field.name)
    value = frontmatter.get(authored) if authored is not None else None
    return declared_raw_value(value, field.declared_type)
''',
)
text = Path("src/okf_parser/typed_tables.py").read_text(encoding="utf-8")
text = text.replace("_raw_value(value, field.declared_type)", "declared_raw_value(value, field.declared_type)")
Path("src/okf_parser/typed_tables.py").write_text(text, encoding="utf-8")

# Apply imports the shared contract/planner and declaration errors.
replace(
    "src/okf_parser/apply.py",
    '''from okf_parser.bundle import validate_path
''',
    '''from okf_parser.bundle import validate_path
from okf_parser.declared_schema import DeclaredSchema, DeclaredSchemaError
''',
)
replace(
    "src/okf_parser/apply.py",
    '''from okf_parser.parser import (
    DocumentParseError,
    concept_id,
    is_reserved_document,
    parse_document_text,
)
''',
    '''from okf_parser.parser import (
    DocumentParseError,
    concept_id,
    is_reserved_document,
    parse_document_text,
)
from okf_parser.typed_tables import (
    TypedTableError,
    TypedTablePlan,
    compile_typed_table_plan,
    discover_declared_schemas,
    duckdb_identifier_key,
    field_input_value,
)
''',
)
replace(
    "src/okf_parser/apply.py",
    '''    from collections.abc import Sequence

    import ibis.backends.duckdb as ibis_duckdb
''',
    '''    from collections.abc import Mapping, Sequence

    import ibis.backends.duckdb as ibis_duckdb

    from okf_parser.models import YamlValue
''',
)

# Typed materialization gets a consumer-specific physical realization:
# raw carrier + VIRTUAL generated projection. Undeclared types keep the
# existing Ibis memtable path unchanged.
needle = '''def _build_table(
    con: IbisConnection,
    table_name: str,
    field_names: Sequence[str],
    concepts: Sequence[_Concept],
) -> None:
'''
idx = Path("src/okf_parser/apply.py").read_text(encoding="utf-8").find(needle)
if idx < 0:
    raise SystemExit("_build_table anchor not found")
insert = r'''
def _typed_table_ddl(plan: TypedTablePlan) -> str:
    columns = [
        '"__okf_path" VARCHAR',
        '"__okf_concept_id" VARCHAR',
        '"__okf_logical_key" VARCHAR',
        '"__okf_body" VARCHAR',
        '"__okf_body_lines" VARCHAR[]',
        '"__okf_frontmatter" VARCHAR',
    ]
    for field in plan.fields:
        if not field.declared:
            columns.append(f"{_quote_ident(field.name)} VARCHAR")
            continue
        raw_name = field.raw_name
        raw_type = field.raw_sql_type
        declared_type = field.declared_type
        if raw_name is None or raw_type is None or declared_type is None:
            msg = f"declared field {field.name!r} has an incomplete typed-table plan"
            raise ApplyError(msg)
        columns.append(f"{_quote_ident(raw_name)} {raw_type}")
        columns.append(
            f"{_quote_ident(field.name)} {declared_type.sql} GENERATED ALWAYS AS "
            f"(TRY_CAST({_quote_ident(raw_name)} AS {declared_type.sql})) VIRTUAL"
        )
    return f"CREATE TEMP TABLE {_quote_ident(plan.concept_type)} ({', '.join(columns)})"


def _typed_insert_columns(plan: TypedTablePlan) -> tuple[str, ...]:
    columns = [
        "__okf_path",
        "__okf_concept_id",
        "__okf_logical_key",
        "__okf_body",
        "__okf_body_lines",
        "__okf_frontmatter",
    ]
    for field in plan.fields:
        if field.declared:
            if field.raw_name is None:
                msg = f"declared field {field.name!r} has no raw carrier"
                raise ApplyError(msg)
            columns.append(field.raw_name)
        else:
            columns.append(field.name)
    return tuple(columns)


def _typed_row_values(concept: _Concept, plan: TypedTablePlan) -> tuple[object, ...]:
    frontmatter = cast("Mapping[str, YamlValue]", concept.frontmatter)
    values: list[object] = [
        concept.relative,
        concept.concept_id,
        concept.concept_id,
        concept.body,
        concept.body.splitlines(),
        concept.raw.frontmatter_text,
    ]
    values.extend(field_input_value(frontmatter, field) for field in plan.fields)
    return tuple(values)


def _build_typed_table(
    con: IbisConnection,
    plan: TypedTablePlan,
    concepts: Sequence[_Concept],
) -> None:
    native = con.raw_sql(_typed_table_ddl(plan))
    columns = _typed_insert_columns(plan)
    rows = [_typed_row_values(concept, plan) for concept in concepts]
    if not rows:
        return
    column_sql = ", ".join(_quote_ident(name) for name in columns)
    placeholders = ", ".join("?" for _ in columns)
    native.executemany(
        f"INSERT INTO {_quote_ident(plan.concept_type)} ({column_sql}) VALUES ({placeholders})",
        rows,
    )


'''
text = Path("src/okf_parser/apply.py").read_text(encoding="utf-8")
text = text[:idx] + insert + text[idx:]
Path("src/okf_parser/apply.py").write_text(text, encoding="utf-8")

# Materialization carries per-type declared/protected metadata alongside the
# public field namespace used by existing diff compilation.
replace(
    "src/okf_parser/apply.py",
    '''class _MaterializeResult:
    fields_by_type: dict[str, list[str]]
    concepts_by_type: dict[str, list[_Concept]]
    structured_by_type: dict[str, frozenset[str]]


def _materialize(con: IbisConnection, concepts: Sequence[_Concept]) -> _MaterializeResult:
''',
    '''class _MaterializeResult:
    fields_by_type: dict[str, list[str]]
    concepts_by_type: dict[str, list[_Concept]]
    structured_by_type: dict[str, frozenset[str]]
    declared_by_type: dict[str, frozenset[str]]
    protected_by_type: dict[str, frozenset[str]]


def _materialize(
    con: IbisConnection,
    concepts: Sequence[_Concept],
    declarations: Mapping[str, DeclaredSchema],
) -> _MaterializeResult:
''',
)
replace(
    "src/okf_parser/apply.py",
    '''    fields_by_type: dict[str, list[str]] = {}
    structured_by_type: dict[str, frozenset[str]] = {}
    for type_name, type_concepts in sorted(by_type.items()):
        kinds = _field_kinds(type_concepts)
        # Checked against every authored key, not just the scalar-writable
        # ones: a structured key under the reserved prefix is just as much a
        # collision, even though it never becomes a column at all.
        _check_reserved_field_names(type_name, [*kinds.scalar, *kinds.structured])
        try:
            _build_table(con, type_name, kinds.scalar, type_concepts)
        except duckdb.CatalogException as exc:
            msg = (
                f'type "{type_name}" collides with another type under DuckDB '
                f"identifier equality: {exc}"
            )
            raise ApplyError(msg) from exc
        # Defense in depth, not a re-implementation of DuckDB's folding rules:
        # verify the catalog actually stored exactly the columns intended,
        # in case DuckDB ever silently dedups or renames on its own.
        created = set(_describe(con, type_name)) - _PROTECTED_COLUMNS
        if created != set(kinds.scalar):
            msg = (
                f'type "{type_name}" columns were not stored as authored: '
                f"expected {sorted(kinds.scalar)}, got {sorted(created)}"
            )
            raise ApplyError(msg)
        fields_by_type[type_name] = kinds.scalar
        structured_by_type[type_name] = kinds.structured
    return _MaterializeResult(
        fields_by_type=fields_by_type,
        concepts_by_type=by_type,
        structured_by_type=structured_by_type,
    )
''',
    '''    fields_by_type: dict[str, list[str]] = {}
    structured_by_type: dict[str, frozenset[str]] = {}
    declared_by_type: dict[str, frozenset[str]] = {}
    protected_by_type: dict[str, frozenset[str]] = {}
    for type_name, type_concepts in sorted(by_type.items()):
        kinds = _field_kinds(type_concepts)
        _check_reserved_field_names(type_name, [*kinds.scalar, *kinds.structured])
        declaration = declarations.get(type_name)
        declared_names: frozenset[str] = frozenset()
        protected = _PROTECTED_COLUMNS
        try:
            if declaration is None:
                _build_table(con, type_name, kinds.scalar, type_concepts)
                public_fields = kinds.scalar
                structured = kinds.structured
            else:
                plan = compile_typed_table_plan(
                    type_name,
                    [cast("Mapping[str, YamlValue]", concept.frontmatter) for concept in type_concepts],
                    declaration,
                )
                _build_typed_table(con, plan, type_concepts)
                public_fields = [field.name for field in plan.fields]
                declared_names = frozenset(field.name for field in plan.fields if field.declared)
                declared_keys = {duckdb_identifier_key(name) for name in declared_names}
                structured = frozenset(
                    name
                    for name in kinds.structured
                    if duckdb_identifier_key(name) not in declared_keys
                )
                raw_names = {
                    field.raw_name
                    for field in plan.fields
                    if field.declared and field.raw_name is not None
                }
                protected = frozenset({*_PROTECTED_COLUMNS, *raw_names})
        except (duckdb.CatalogException, TypedTableError) as exc:
            msg = (
                f'type "{type_name}" could not be materialized under DuckDB identifier '
                f"semantics: {exc}"
            )
            raise ApplyError(msg) from exc

        created = set(_describe(con, type_name)) - protected
        if created != set(public_fields):
            msg = (
                f'type "{type_name}" columns were not stored as planned: '
                f"expected {sorted(public_fields)}, got {sorted(created)}"
            )
            raise ApplyError(msg)
        fields_by_type[type_name] = public_fields
        structured_by_type[type_name] = structured
        declared_by_type[type_name] = declared_names
        protected_by_type[type_name] = protected
    return _MaterializeResult(
        fields_by_type=fields_by_type,
        concepts_by_type=by_type,
        structured_by_type=structured_by_type,
        declared_by_type=declared_by_type,
        protected_by_type=protected_by_type,
    )
''',
)

# ALTER checks become aware of declared/public and compiler-owned raw fields.
replace(
    "src/okf_parser/apply.py",
    '''def _check_alter_shape(
    before: dict[str, dict[str, str]], after: dict[str, dict[str, str]], query: str
) -> tuple[str, str, str] | None:
''',
    '''def _check_alter_shape(
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
    query: str,
    declared_by_type: Mapping[str, frozenset[str]],
    protected_by_type: Mapping[str, frozenset[str]],
) -> tuple[str, str, str] | None:
''',
)
replace(
    "src/okf_parser/apply.py",
    '''    is_add = not removed and len(added) == 1
    is_drop = len(removed) == 1 and not added
    is_rename = len(removed) == 1 and len(added) == 1
    if not (is_add or is_drop or is_rename):
        msg = f"ALTER statement must add, drop, or rename exactly one column: {query}"
        raise ApplyError(msg)
    if is_rename:
        (old_name,) = removed
        (new_name,) = added
        return (type_name, old_name, new_name)
    return None
''',
    '''    is_add = not removed and len(added) == 1
    is_drop = len(removed) == 1 and not added
    is_rename = len(removed) == 1 and len(added) == 1
    if not (is_add or is_drop or is_rename):
        msg = f"ALTER statement must add, drop, or rename exactly one column: {query}"
        raise ApplyError(msg)

    protected_keys = {
        duckdb_identifier_key(name) for name in protected_by_type.get(type_name, frozenset())
    }
    changed_keys = {duckdb_identifier_key(name) for name in removed | added}
    if protected_keys & changed_keys:
        msg = f"ALTER statement touched a compiler-owned protected column: {query}"
        raise ApplyError(msg)

    declared_keys = {
        duckdb_identifier_key(name) for name in declared_by_type.get(type_name, frozenset())
    }
    if is_add and declared_keys & {duckdb_identifier_key(name) for name in added}:
        msg = f"ALTER statement cannot add a field still owned by the declaration: {query}"
        raise ApplyError(msg)
    if is_rename:
        (old_name,) = removed
        (new_name,) = added
        if duckdb_identifier_key(old_name) in declared_keys or duckdb_identifier_key(new_name) in declared_keys:
            msg = f"ALTER statement cannot rename a declared field: {query}"
            raise ApplyError(msg)
        return (type_name, old_name, new_name)
    return None
''',
)
replace(
    "src/okf_parser/apply.py",
    '''def _run_transaction(
    con: IbisConnection,
    type_names: Sequence[str],
    alter_queries: list[str],
    update_query: str,
) -> tuple[frozenset[str], dict[str, dict[str, str]]]:
''',
    '''def _run_transaction(
    con: IbisConnection,
    type_names: Sequence[str],
    alter_queries: list[str],
    update_query: str,
    declared_by_type: Mapping[str, frozenset[str]],
    protected_by_type: Mapping[str, frozenset[str]],
) -> tuple[frozenset[str], dict[str, dict[str, str]]]:
''',
)
replace(
    "src/okf_parser/apply.py",
    '''            rename = _check_alter_shape(before, after, query)
''',
    '''            rename = _check_alter_shape(
                before,
                after,
                query,
                declared_by_type,
                protected_by_type,
            )
''',
)

# Schema and row checks use dynamic protected sets; generated public declared
# fields remain visible but read-only and are excluded from YAML value diffing.
replace(
    "src/okf_parser/apply.py",
    '''def _check_result_schema(
    after_schema: dict[str, str], before_schema: dict[str, str], structured: frozenset[str]
) -> None:
''',
    '''def _check_result_schema(
    after_schema: dict[str, str],
    before_schema: dict[str, str],
    structured: frozenset[str],
    protected: frozenset[str],
) -> None:
''',
)
text = Path("src/okf_parser/apply.py").read_text(encoding="utf-8")
text = text.replace("missing_protected = _PROTECTED_COLUMNS - after_cols", "missing_protected = protected - after_cols")
Path("src/okf_parser/apply.py").write_text(text, encoding="utf-8")
replace(
    "src/okf_parser/apply.py",
    '''def _check_result_rows(before: ibis.Table, after: ibis.Table) -> None:
''',
    '''def _check_result_rows(
    before: ibis.Table, after: ibis.Table, protected: frozenset[str]
) -> None:
''',
)
text = Path("src/okf_parser/apply.py").read_text(encoding="utf-8")
text = text.replace("for column in sorted(_PROTECTED_COLUMNS):", "for column in sorted(protected):")
Path("src/okf_parser/apply.py").write_text(text, encoding="utf-8")
replace(
    "src/okf_parser/apply.py",
    '''def _compile_row_diff(  # each argument is a distinct compile input.
    concept: _Concept,
    field_names: Sequence[str],
    removed_columns: frozenset[str],
    renamed_from: dict[str, str],
    after_row: dict[str, object],
    *,
    selected: bool,
) -> dict[str, str | None]:
''',
    '''def _compile_row_diff(  # each argument is a distinct compile input.
    concept: _Concept,
    field_names: Sequence[str],
    removed_columns: frozenset[str],
    renamed_from: dict[str, str],
    after_row: dict[str, object],
    read_only_fields: frozenset[str],
    *,
    selected: bool,
) -> dict[str, str | None]:
''',
)
replace(
    "src/okf_parser/apply.py",
    '''        if name in removed_columns:
            if name in concept.frontmatter:
                diff[name] = None
            continue
        origin = renamed_from.get(name)
''',
    '''        if name in removed_columns:
            if name in concept.frontmatter:
                diff[name] = None
            continue
        if name in read_only_fields:
            continue
        origin = renamed_from.get(name)
''',
)
replace(
    "src/okf_parser/apply.py",
    '''    selected_ids, renamed_by_type = _run_transaction(
        con, list(materialized.fields_by_type), alter_queries, update_query
    )
''',
    '''    selected_ids, renamed_by_type = _run_transaction(
        con,
        list(materialized.fields_by_type),
        alter_queries,
        update_query,
        materialized.declared_by_type,
        materialized.protected_by_type,
    )
''',
)
replace(
    "src/okf_parser/apply.py",
    '''    after_schema = _describe(con, touched)
    structured = materialized.structured_by_type[touched]
    _check_result_schema(after_schema, snapshots[touched].schema, structured)

    _check_result_rows(snapshots[touched].table, con.table(touched))

    after_rows = _fetch_rows(con, touched)
    field_names = materialized.fields_by_type[touched]
    all_field_names = sorted((set(after_schema) | set(field_names)) - _PROTECTED_COLUMNS)
    removed_columns = frozenset(snapshots[touched].schema) - set(after_schema) - _PROTECTED_COLUMNS
''',
    '''    after_schema = _describe(con, touched)
    structured = materialized.structured_by_type[touched]
    protected = materialized.protected_by_type[touched]
    declared = materialized.declared_by_type[touched]
    _check_result_schema(after_schema, snapshots[touched].schema, structured, protected)

    _check_result_rows(snapshots[touched].table, con.table(touched), protected)

    after_rows = _fetch_rows(con, touched)
    field_names = materialized.fields_by_type[touched]
    all_field_names = sorted((set(after_schema) | set(field_names)) - protected)
    removed_columns = frozenset(snapshots[touched].schema) - set(after_schema) - protected
''',
)
replace(
    "src/okf_parser/apply.py",
    '''                renamed_from,
                after_rows[concept.concept_id],
                selected=concept.concept_id in selected_ids,
''',
    '''                renamed_from,
                after_rows[concept.concept_id],
                declared,
                selected=concept.concept_id in selected_ids,
''',
)

# Public apply gets the same spec-template opt-in as schema and duckdb.
replace(
    "src/okf_parser/apply.py",
    '''    write: bool = False,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
''',
    '''    write: bool = False,
    exclude: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, object]:
''',
)
replace(
    "src/okf_parser/apply.py",
    '''        con = ibis.duckdb.connect()
        materialized = _materialize(con, concepts)
        outcome = _execute_script(con, materialized, alter_queries, update_query)
    except ApplyError as exc:
''',
    '''        declarations = discover_declared_schemas(
            root,
            {concept.concept_type for concept in concepts},
            spec_template,
        )
        con = ibis.duckdb.connect()
        if declarations:
            con.raw_sql("SET TimeZone = 'UTC'")
        materialized = _materialize(con, concepts, declarations)
        outcome = _execute_script(con, materialized, alter_queries, update_query)
    except (ApplyError, DeclaredSchemaError, TypedTableError) as exc:
''',
)

replace(
    "src/okf_parser/service.py",
    '''    write: bool = False,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
''',
    '''    write: bool = False,
    exclude: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, object]:
''',
)
replace(
    "src/okf_parser/service.py",
    '''        write=write,
        exclude=exclude,
    )
''',
    '''        write=write,
        exclude=exclude,
        spec_template=spec_template,
    )
''',
)
replace(
    "src/okf_parser/cli.py",
    '''    write: bool = False,
    exclude: RepeatableStrings = None,
) -> CliResult[JsonPayload]:
''',
    '''    write: bool = False,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> CliResult[JsonPayload]:
''',
)
replace(
    "src/okf_parser/cli.py",
    '''        write=write,
        exclude=exclude or (),
    )
''',
    '''        write=write,
        exclude=exclude or (),
        spec_template=spec_template,
    )
''',
)

# Docs/release bookkeeping.
replace(
    "docs/cli.md",
    '''uv run okf-parser apply path/to/bundle --sql SQL [--write] [--exclude PATTERN]...
''',
    '''uv run okf-parser apply path/to/bundle --sql SQL [--write] [--spec-template TEMPLATE] [--exclude PATTERN]...
''',
)
replace(
    "docs/cli.md",
    '''`--write` is required to touch the bundle. Without it the command computes and
reports the candidate changes. Exits `1` when `payload["succeeded"]` is false,
including validation or write-conflict failures.
''',
    '''`--spec-template TEMPLATE` opts declared fields into RFC 0006's typed query
surface. Each declared value keeps a compiler-owned raw carrier and appears to SQL
as a DuckDB virtual generated `TRY_CAST` column. Declared columns are queryable but
not directly writable; undeclared scalar fields retain RFC 0005 write semantics.

`--write` is required to touch the bundle. Without it the command computes and
reports the candidate changes. Exits `1` when `payload["succeeded"]` is false,
including validation or write-conflict failures.
''',
)

for path in (
    "pyproject.toml",
    "typescript/package.json",
    "typescript-duckdb/package.json",
    "typescript/src/version.ts",
):
    target = Path(path)
    target.write_text(target.read_text(encoding="utf-8").replace("0.17.0", "0.18.0"), encoding="utf-8")

Path("changelog/0.18.0.md").write_text(
    '''---
type: Release
title: okf-parser 0.18.0
description: query RFC 0006 declared types inside apply without making typed values writable
---

# okf-parser 0.18.0

## Typed `apply`

`apply --spec-template` now uses the same RFC 0006 logical field plan as persistent
DuckDB materialization, with a consumer-appropriate physical representation:

- declared source text/list values live in compiler-owned `__okf_raw_*` carriers;
- declared public fields are DuckDB `GENERATED ... VIRTUAL` columns using per-value
  `TRY_CAST`, with the ephemeral connection pinned to UTC;
- typed fields can participate in `WHERE` clauses and expressions while DuckDB's
  binder rejects direct `UPDATE` assignments to them;
- raw carrier columns are dynamically protected from schema/value tampering;
- dropping a declared public field remains a logical field deletion, while renaming
  it or re-adding a still-declared name is rejected;
- generated declared values are excluded from frontmatter writeback, so Python never
  serializes a `Decimal`, timestamp, UUID or list back by accident;
- malformed declaration SQL is an explicit apply error; row-level cast divergence is
  not — the typed projection is simply `NULL` for that row.

Without `--spec-template`, RFC 0005 apply behavior is unchanged.
''',
    encoding="utf-8",
)

# Focused typed-apply tests, including the DuckDB dependency/ALTER question.
path = Path("tests/test_apply.py")
text = path.read_text(encoding="utf-8")
text += r'''


def _typed_spec(tmp_path: Path, ddl: str) -> str:
    spec = tmp_path / "docs" / "types"
    spec.mkdir(parents=True, exist_ok=True)
    _write(spec / "rotina.schema.sql", ddl)
    return "docs/types/{slug}.md"


def test_typed_apply_queries_decimal_without_rewriting_declared_value(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\ncusto: 20.75\nstatus: low\n---\n")
    template = _typed_spec(
        tmp_path,
        'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n',
    )

    result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET status = \'high\' WHERE custo > 15',
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    assert _changed_paths(result) == ["r2.md"]
    assert "custo: 10.50" in _read(tmp_path / "r1.md")
    assert "custo: 20.75" in _read(tmp_path / "r2.md")
    assert "status: high" in _read(tmp_path / "r2.md")


def test_typed_apply_divergent_value_is_null_and_still_queryable(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: n/a\nstatus: low\n---\n")
    template = _typed_spec(
        tmp_path,
        'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n',
    )

    result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET status = \'bad\' WHERE custo IS NULL',
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "custo: n/a" in text
    assert "status: bad" in text


def test_typed_apply_rejects_direct_write_to_generated_declared_field(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET custo = 12.50 WHERE TRUE',
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "generated" in _error(result).lower()


def test_typed_apply_rejects_raw_carrier_tamper(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET __okf_raw_custo = \'99.00\' WHERE TRUE',
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "protected" in _error(result).lower()


def test_typed_apply_drop_declared_field_removes_frontmatter(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" DROP COLUMN custo; '
            'UPDATE "Rotina" SET status = status WHERE FALSE'
        ),
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    assert "custo:" not in _read(tmp_path / "r1.md")


def test_typed_apply_rejects_rename_of_declared_field(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" RENAME COLUMN custo TO valor; '
            'UPDATE "Rotina" SET status = status WHERE FALSE'
        ),
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "declared field" in _error(result).lower()


def test_typed_apply_rejects_readding_dropped_declared_name(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" DROP COLUMN custo; '
            'ALTER TABLE "Rotina" ADD COLUMN custo VARCHAR; '
            'UPDATE "Rotina" SET status = status WHERE FALSE'
        ),
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "owned by the declaration" in _error(result).lower()


def test_typed_apply_queries_declared_list_without_serializing_it(tmp_path: Path) -> None:
    _write(
        tmp_path / "r1.md",
        "---\ntype: Rotina\ntags:\n  - 1\n  - 2\nstatus: low\n---\n",
    )
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (tags BIGINT[]);\n')

    result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET status = \'hit\' WHERE list_contains(tags, 2)',
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "status: hit" in text
    assert "  - 1" in text and "  - 2" in text


def test_typed_apply_declared_unobserved_field_exists_as_null(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (futuro BIGINT);\n')

    result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET status = \'missing\' WHERE futuro IS NULL',
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    assert "status: missing" in _read(tmp_path / "r1.md")
    assert "futuro:" not in _read(tmp_path / "r1.md")


def test_typed_apply_invalid_declaration_is_explicit_error(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, "CREATE TABLE broken")

    result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET status = \'x\' WHERE TRUE',
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "declared schema" in _error(result).lower()


def test_typed_apply_keeps_undeclared_alter_add_semantics(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" ADD COLUMN status VARCHAR; '
            'UPDATE "Rotina" SET status = \'ok\' WHERE custo > 5'
        ),
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    assert "status: ok" in _read(tmp_path / "r1.md")
'''
path.write_text(text, encoding="utf-8")
