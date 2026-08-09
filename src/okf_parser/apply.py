"""Relational writes to frontmatter fields via SQL, per RFC 0005.

`apply` materializes every concept type as its own DuckDB table (named for
the exact authored ``type`` value, quoted) inside one in-memory database,
then hands the caller's ``--sql`` to it: zero or more leading
``ALTER TABLE`` statements, followed by exactly one ``UPDATE``, run as a
single transaction. DuckDB's own parser, binder and catalog resolve every
identifier and every ALTER's effect; this module never re-derives "what did
the script mean to do." Instead, for whichever single type table the script
touched, every concept's target frontmatter is *compiled directly from the
final relational state*: a column absent (or NULL) from the final row means
the key is absent from the document, a column present with a non-NULL value
means the key holds exactly that value. Two more pieces of information feed
the compile, both read from DuckDB's own catalog and `RETURNING` output
rather than by parsing the script's SQL text: which rows the trailing
UPDATE's WHERE actually selected (needed only to resolve a value that was,
and still is, NULL - otherwise indistinguishable from a row the script never
touched at all), and which columns are the final name of a rename chain
(needed to carry a value structurally to every row that authored the old
name, independent of selection). Given those, the same final state always
compiles to the same document, regardless of how many statements, or which
ones, produced it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast

import duckdb
import ibis
from ruamel.yaml import YAML

from okf_parser.bundle import validate_path
from okf_parser.declared_schema import DeclaredSchema, DeclaredSchemaError
from okf_parser.models import Severity
from okf_parser.typed_tables import (
    TypedTableError,
    TypedTablePlan,
    compile_typed_table_plan,
    discover_declared_schemas,
    duckdb_identifier_key,
    field_input_value,
)
from okf_parser.write_support import (
    BundleSnapshot as _BundleSnapshot,
)
from okf_parser.write_support import (
    ConceptSnapshot as _Concept,
)
from okf_parser.write_support import (
    RawDocument as _RawDocument,
)
from okf_parser.write_support import (
    WriteResult as ApplyResult,
)
from okf_parser.write_support import (
    WriteSupportError,
)
from okf_parser.write_support import (
    snapshot_bundle as _snapshot_bundle,
)
from okf_parser.write_support import (
    stage_validate_write as _stage_validate_write,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Any

    import ibis.backends.duckdb as ibis_duckdb

    IbisConnection = ibis_duckdb.Backend

_OKF_PREFIX = "__okf_"
_IDENTITY_COLUMNS = ("__okf_path", "__okf_concept_id", "__okf_logical_key")
_BODY_COLUMNS = ("__okf_body", "__okf_body_lines")
_FRONTMATTER_TEXT_COLUMNS = ("__okf_frontmatter",)
_PROTECTED_COLUMNS = frozenset({*_IDENTITY_COLUMNS, *_BODY_COLUMNS, *_FRONTMATTER_TEXT_COLUMNS})
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(.*?)(?:\r?\n)?---[ \t]*(?:\r?\n(.*))?\Z",
    re.DOTALL,
)


class ApplyError(ValueError):
    """Raised when `--sql` is malformed or a script violates the v1 contract."""


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _round_trips_losslessly(yaml: YAML, frontmatter_text: str) -> bool:
    try:
        data = yaml.load(frontmatter_text)
        buffer = StringIO()
        yaml.dump(data, buffer)
    except Exception:  # any load/dump failure means "cannot round-trip"
        return False
    return buffer.getvalue().rstrip("\n") == frontmatter_text.rstrip("\n")


@dataclass(frozen=True, slots=True)
class _FieldKinds:
    """A type's authored keys, split by whether every observed value is scalar."""

    scalar: list[str]
    structured: frozenset[str]


def _field_kinds(concepts: Sequence[_Concept]) -> _FieldKinds:
    """Split a type's authored keys into scalar-everywhere and structured-somewhere.

    A key observed as a list or map on even one document is excluded from the
    writable namespace entirely, on every document of that type - not just
    NULLed out on the documents where it's structured - so a value that would
    otherwise be silently overwritten or dropped never becomes reachable from
    SQL in the first place. `structured` is kept (not just discarded) so a
    later `ADD COLUMN` can't reintroduce one of these names as an ordinary
    writable column and have the compiler overwrite or delete the original
    structured value.
    """
    scalar_keys: set[str] = set()
    structured_keys: set[str] = set()
    for concept in concepts:
        for key, value in concept.frontmatter.items():
            if isinstance(value, str) or value is None:
                scalar_keys.add(key)
            else:
                structured_keys.add(key)
    return _FieldKinds(
        scalar=sorted(scalar_keys - structured_keys), structured=frozenset(structured_keys)
    )


def _check_reserved_field_names(type_name: str, field_names: Sequence[str]) -> None:
    """Reject authored fields under the reserved `__okf_` prefix.

    Checked ASCII-case-insensitively against this fixed ASCII literal only -
    not a general identifier-collision emulation of DuckDB's own folding
    rules, which `_materialize` verifies by inspecting the catalog after
    table creation instead of by guessing at them here.
    """
    for name in field_names:
        if name[: len(_OKF_PREFIX)].lower() == _OKF_PREFIX:
            msg = (
                f'type "{type_name}" has an authored field colliding with the '
                f"reserved __okf_ prefix: {name}"
            )
            raise ApplyError(msg)


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
    frontmatter = cast("Mapping[str, Any]", concept.frontmatter)
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


def _build_table(
    con: IbisConnection,
    table_name: str,
    field_names: Sequence[str],
    concepts: Sequence[_Concept],
) -> None:
    columns: dict[str, list[object]] = {
        "__okf_path": [],
        "__okf_concept_id": [],
        "__okf_logical_key": [],
        "__okf_body": [],
        "__okf_body_lines": [],
        "__okf_frontmatter": [],
    }
    for name in field_names:
        columns[name] = []
    for concept in concepts:
        columns["__okf_path"].append(concept.relative)
        columns["__okf_concept_id"].append(concept.concept_id)
        columns["__okf_logical_key"].append(concept.concept_id)
        columns["__okf_body"].append(concept.body)
        columns["__okf_body_lines"].append(concept.body.splitlines())
        columns["__okf_frontmatter"].append(concept.raw.frontmatter_text)
        for name in field_names:
            value = concept.frontmatter.get(name)
            columns[name].append(value if isinstance(value, str) else None)

    schema = ibis.schema(
        {
            "__okf_path": "string",
            "__okf_concept_id": "string",
            "__okf_logical_key": "string",
            "__okf_body": "string",
            "__okf_body_lines": "array<string>",
            "__okf_frontmatter": "string",
            **dict.fromkeys(field_names, "string"),
        }
    )
    table = ibis.memtable(columns, schema=schema)
    # ibis's `create_table` builds the CREATE TABLE statement itself (via
    # sqlglot, properly quoting the identifier) and hands the in-memory
    # table straight to DuckDB - no intermediate staging relation under any
    # name, fixed or random, ever exists for a real `type` to shadow or
    # collide with. A genuine collision still raises duckdb.CatalogException,
    # same as any other CREATE TABLE.
    con.create_table(table_name, obj=table, temp=True)


@dataclass(frozen=True, slots=True)
class _MaterializeResult:
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
    """Build one table per type inside the script's catalog; return its field names.

    No pre-mutation snapshot is created as a table here: a real `type` could be
    authored with a name that collides with an internal snapshot table, and
    any table in this catalog is addressable by the caller's own `--sql`. The
    pre-image instead lives entirely in Python (see `_snapshot_types`), never
    exposed to the script.
    """
    by_type: dict[str, list[_Concept]] = {}
    for concept in concepts:
        by_type.setdefault(concept.concept_type, []).append(concept)

    fields_by_type: dict[str, list[str]] = {}
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
                    [cast("Mapping[str, Any]", concept.frontmatter) for concept in type_concepts],
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
        except duckdb.CatalogException as exc:
            msg = (
                f'type "{type_name}" collides with another type under DuckDB '
                f"identifier equality: {exc}"
            )
            raise ApplyError(msg) from exc
        except TypedTableError as exc:
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


@dataclass(frozen=True, slots=True)
class _TypeSnapshot:
    schema: dict[str, str]
    table: ibis.Table


def _snapshot_types(
    con: IbisConnection, fields_by_type: dict[str, list[str]]
) -> dict[str, _TypeSnapshot]:
    """Capture every type table's schema and full relation before the script runs.

    Held only in Python, as a detached `ibis.memtable` (data pulled off
    `con` and owned independently), never as a queryable table in `con`'s
    catalog, so the caller's `--sql` cannot address, corrupt, or collide a
    type against it. Kept as a relation rather than a dict-of-dicts so
    comparing it against the post-script state is Ibis's own
    `difference`/`join`, not a hand-rolled Python equality walk over every
    row.
    """
    return {
        type_name: _TypeSnapshot(
            schema=_describe(con, type_name),
            table=ibis.memtable(con.table(type_name).to_pyarrow()),
        )
        for type_name in fields_by_type
    }


def _extract_statements(sql: str) -> list[duckdb.Statement]:
    con = duckdb.connect()
    try:
        statements = con.extract_statements(sql)
    except duckdb.Error as exc:
        msg = f"--sql could not be parsed: {exc}"
        raise ApplyError(msg) from exc
    finally:
        con.close()
    if not statements:
        msg = "--sql must contain at least one statement"
        raise ApplyError(msg)
    return statements


def _parse_script(sql: str) -> tuple[list[str], str]:
    """Validate the script's *shape* only: leading ALTERs, one trailing UPDATE.

    What each ALTER actually does is never inspected here - DuckDB executes
    it and the catalog says what changed, in `_execute_script`.
    """
    *leading, trailing = _extract_statements(sql)
    if not str(trailing.type).endswith("UPDATE"):
        msg = "--sql must end with exactly one UPDATE statement"
        raise ApplyError(msg)

    alter_queries: list[str] = []
    for statement in leading:
        query = statement.query.strip().rstrip(";")
        if not str(statement.type).endswith("ALTER"):
            msg = (
                "--sql may only contain leading ALTER TABLE statements before "
                f"the final UPDATE, found: {query}"
            )
            raise ApplyError(msg)
        alter_queries.append(query)

    update_query = trailing.query.strip().rstrip(";")
    return alter_queries, update_query


@dataclass(frozen=True, slots=True)
class _RowDiff:
    concept_id: str
    changed_fields: dict[str, str | None]


@dataclass(frozen=True, slots=True)
class _ScriptOutcome:
    touched_type: str | None
    row_diffs: tuple[_RowDiff, ...] = ()


def _describe(con: IbisConnection, table: str) -> dict[str, str]:
    rows = con.raw_sql(f"DESCRIBE {_quote_ident(table)}").fetchall()
    return {row[0]: row[1] for row in rows}


def _fetch_rows(con: IbisConnection, table: str) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for record in con.table(table).to_pyarrow().to_pylist():
        rows[record["__okf_concept_id"]] = record
    return rows


def _check_alter_shape(
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
    query: str,
    declared_by_type: Mapping[str, frozenset[str]],
    protected_by_type: Mapping[str, frozenset[str]],
) -> tuple[str, str, str] | None:
    """Reject any ALTER whose catalog delta isn't a single add/drop/rename column.

    The RFC promises leading statements are limited to
    ``ADD/DROP/RENAME COLUMN``; DuckDB's grammar allows other `ALTER TABLE`
    forms (a type change, a constraint, a default) that a name/type diff
    wouldn't otherwise notice, since `DESCRIBE` only reports name and type.
    Checked against the real catalog per statement, not by parsing the SQL.
    Returns ``(type_name, old_name, new_name)`` when the statement was a
    rename, so the caller can track a value's structural carry across it -
    scoped to the exact type the rename happened on, not globally - ``None``
    otherwise.
    """
    changed_types = [type_name for type_name in before if before[type_name] != after[type_name]]
    if len(changed_types) != 1:
        msg = f"ALTER statement must affect exactly one type's table: {query}"
        raise ApplyError(msg)
    type_name = changed_types[0]
    before_cols, after_cols = before[type_name], after[type_name]
    removed = set(before_cols) - set(after_cols)
    added = set(after_cols) - set(before_cols)
    kept_retyped = {
        column
        for column in set(before_cols) & set(after_cols)
        if before_cols[column] != after_cols[column]
    }
    if kept_retyped:
        msg = f"ALTER statement changed an existing column's type: {query}"
        raise ApplyError(msg)
    is_add = not removed and len(added) == 1
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
        if (
            duckdb_identifier_key(old_name) in declared_keys
            or duckdb_identifier_key(new_name) in declared_keys
        ):
            msg = f"ALTER statement cannot rename a declared field: {query}"
            raise ApplyError(msg)
        return (type_name, old_name, new_name)
    return None


def _run_transaction(
    con: IbisConnection,
    type_names: Sequence[str],
    alter_queries: list[str],
    update_query: str,
    declared_by_type: Mapping[str, frozenset[str]],
    protected_by_type: Mapping[str, frozenset[str]],
) -> tuple[frozenset[str], dict[str, dict[str, str]]]:
    """Run the script; return selected concept IDs and the rename chain, per type.

    `RETURNING __okf_concept_id` is the only reliable source for "which rows
    did the trailing UPDATE's WHERE select": a row the WHERE matched but
    whose SET didn't actually change any value looks, in a before/after
    comparison, identical to a row the WHERE never touched. Asking DuckDB
    directly instead of parsing the SET list keeps the "no SQL intent
    parsing" property the rest of this module holds to.

    The rename chain (old column name -> its current name, folded through
    every leading `RENAME COLUMN`) is tracked the same way - from each
    statement's own catalog delta, never its text - because a renamed
    column's value is carried structurally to every row that had the old
    name, independent of whether the trailing UPDATE's `WHERE` selected that
    row at all. Kept one chain per type, not one shared chain across every
    type's table: a rename on one type must never bleed into another type
    that happens to have a column under the same name. Identity mappings
    (a chain that renamed a column back to its original name) are dropped
    once the whole script has run, so a cancel-out chain on the touched
    type's own columns doesn't get treated as a structural carry either.
    """
    renamed_by_type: dict[str, dict[str, str]] = {}
    con.raw_sql("BEGIN TRANSACTION")
    try:
        for query in alter_queries:
            before = {t: _describe(con, t) for t in type_names}
            con.raw_sql(query)
            after = {t: _describe(con, t) for t in type_names}
            rename = _check_alter_shape(
                before,
                after,
                query,
                declared_by_type,
                protected_by_type,
            )
            if rename is not None:
                type_name, old_name, new_name = rename
                type_renamed = renamed_by_type.setdefault(type_name, {})
                origin = next((k for k, v in type_renamed.items() if v == old_name), old_name)
                type_renamed[origin] = new_name
        cursor = con.raw_sql(f"{update_query} RETURNING __okf_concept_id")
        selected_ids = frozenset(row[0] for row in cursor.fetchall())
    except duckdb.Error as exc:
        con.raw_sql("ROLLBACK")
        msg = f"script failed: {exc}"
        raise ApplyError(msg) from exc
    except ApplyError:
        con.raw_sql("ROLLBACK")
        raise
    con.raw_sql("COMMIT")
    for type_renamed in renamed_by_type.values():
        for origin in [key for key, target in type_renamed.items() if key == target]:
            del type_renamed[origin]
    return selected_ids, renamed_by_type


def _relation_differs(before: ibis.Table, after: ibis.Table) -> bool:
    """Whether two same-shaped relations hold different rows.

    Compared as Ibis expressions - `before` a detached `ibis.memtable`,
    `after` a live view of the post-script table - which Ibis itself
    resolves against whichever backend executes the expression, needing no
    explicit registration. `difference` is exactly the "did anything
    change" question; there's no reason to fetch every row into Python
    just to walk an equality.
    """
    if before.difference(after).count().execute() > 0:
        return True
    return after.difference(before).count().execute() > 0


def _find_touched_type(
    con: IbisConnection,
    snapshots: dict[str, _TypeSnapshot],
) -> str | None:
    try:
        changed_types = []
        for type_name, snapshot in snapshots.items():
            after_schema = _describe(con, type_name)
            if after_schema != snapshot.schema:
                changed_types.append(type_name)
                continue
            if _relation_differs(snapshot.table, con.table(type_name)):
                changed_types.append(type_name)
    except duckdb.Error as exc:
        msg = f"script failed: {exc}"
        raise ApplyError(msg) from exc
    if len(changed_types) > 1:
        msg = f"script touched more than one type's table: {sorted(changed_types)}"
        raise ApplyError(msg)
    return changed_types[0] if changed_types else None


def _type_of_selected(
    concepts_by_type: dict[str, list[_Concept]], selected_ids: frozenset[str]
) -> str | None:
    """Which type owns any of the UPDATE's `RETURNING`-selected concept IDs.

    A row the WHERE matched but whose SET didn't change any stored value is
    invisible to `_find_touched_type`'s before/after comparison - this is
    the fallback that still recognizes its type as touched.
    """
    if not selected_ids:
        return None
    for type_name, concepts in concepts_by_type.items():
        if any(concept.concept_id in selected_ids for concept in concepts):
            return type_name
    return None


def _check_result_schema(
    after_schema: dict[str, str],
    before_schema: dict[str, str],
    structured: frozenset[str],
    protected: frozenset[str],
) -> None:
    after_cols = set(after_schema)
    before_cols = set(before_schema)
    missing_protected = protected - after_cols
    if missing_protected:
        msg = f"script removed protected columns: {sorted(missing_protected)}"
        raise ApplyError(msg)
    for column in after_cols & before_cols:
        if after_schema[column] != before_schema[column]:
            msg = (
                f'column "{column}" changed type from {before_schema[column]} '
                f"to {after_schema[column]}"
            )
            raise ApplyError(msg)
    # ASCII-case-folded, matching DuckDB's own identifier equality: a bare or
    # quoted "Tags"/"TAGS"/"tags" are all the same column to DuckDB, so a
    # structured "Tags" is exactly as reserved as a differently-cased
    # reintroduction of it.
    structured_folded = {name.lower() for name in structured}
    for column in after_cols - before_cols:
        # ASCII-case-insensitive, matching the authored-side reserved-prefix
        # check: DuckDB folds unquoted/quoted ASCII case, so "__OKF_custom"
        # is exactly as reserved as "__okf_custom".
        if column[: len(_OKF_PREFIX)].lower() == _OKF_PREFIX:
            msg = f'column "{column}" collides with the reserved __okf_ prefix'
            raise ApplyError(msg)
        if column.lower() in structured_folded:
            msg = (
                f'column "{column}" reintroduces a structured (list/map) field '
                "under DuckDB identifier equality, which is never a writable column"
            )
            raise ApplyError(msg)
        if after_schema[column].upper() != "VARCHAR":
            msg = f'column "{column}" must be VARCHAR, got {after_schema[column]}'
            raise ApplyError(msg)


def _check_result_rows(before: ibis.Table, after: ibis.Table, protected: frozenset[str]) -> None:
    """Row identity/cardinality and protected-column tamper checks, via Ibis.

    `before` is a detached `ibis.memtable`, `after` a live view of the
    post-script table - the same pairing as `_relation_differs`, needing no
    explicit registration.
    """
    before_ids = before.select("__okf_concept_id")
    after_ids = after.select("__okf_concept_id")
    changed_cardinality = before_ids.difference(after_ids).union(after_ids.difference(before_ids))
    if changed_cardinality.count().execute() > 0:
        msg = "script changed row identity or cardinality"
        raise ApplyError(msg)

    after_aliased = after.select(**{f"{name}__after": after[name] for name in after.columns})
    joined = before.join(
        after_aliased,
        before["__okf_concept_id"] == after_aliased["__okf_concept_id__after"],
    )
    for column in sorted(protected):
        # column is one of the fixed _PROTECTED_COLUMNS names, not caller input.
        tampered = joined.filter(~joined[column].identical_to(joined[f"{column}__after"]))
        row = tampered.select("__okf_concept_id").limit(1).execute()
        if not row.empty:
            msg = f'row "{row.iloc[0, 0]}" changed protected column "{column}"'
            raise ApplyError(msg)


class _NotApplicable:
    """Sentinel: this field needs no diff entry at all - distinct from `None` (delete)."""


_NOT_APPLICABLE = _NotApplicable()


def _compile_field_value(final_value: object, *, was_present: bool) -> str | None | _NotApplicable:
    """Unconditionally compile one field's target: a delete only if it was ever authored."""
    target = final_value if isinstance(final_value, str) else None
    if target is None:
        return None if was_present else _NOT_APPLICABLE
    return target


def _compile_changed_field_value(
    final_value: object, *, was_present: bool, current_value: object, selected: bool
) -> str | None | _NotApplicable:
    """Compile a kept/added column's target: on a real value change, or on selection.

    A kept-or-added column can change its stored value without that row ever
    being selected by the trailing `UPDATE`'s `WHERE` - a `DROP COLUMN`
    immediately followed by `ADD COLUMN` of the same name resets every row
    to NULL (or to an `ADD COLUMN ... DEFAULT`'s literal) structurally, not
    row by row. Whenever the final value is a string, or differs from the
    row's own original value, that's real information regardless of
    selection. Only the NULL-was-already-NULL case is genuinely ambiguous
    without knowing selection - a `RETURNING`-selected row explicitly set to
    NULL always deletes the key, but an unselected row whose value was
    already absent/null must be left alone.
    """
    target = final_value if isinstance(final_value, str) else None
    if target is not None:
        return _NOT_APPLICABLE if current_value == target else target
    if current_value is not None:
        return None if was_present else _NOT_APPLICABLE
    if not selected:
        return _NOT_APPLICABLE
    return None if was_present else _NOT_APPLICABLE


def _compile_row_diff(  # each argument is a distinct compile input.
    concept: _Concept,
    field_names: Sequence[str],
    removed_columns: frozenset[str],
    renamed_from: dict[str, str],
    after_row: dict[str, object],
    read_only_fields: frozenset[str],
    *,
    selected: bool,
) -> dict[str, str | None]:
    """Recompute one concept's target frontmatter purely from the final relation.

    Deliberately blind to *how* the final state was reached - a rename, a
    drop-then-recreate, a chain of renames, and a direct value update that
    happens to land on the same result all compile to the same diff. Three
    cases:

    - a column no longer in the final schema at all (dropped, or renamed
      away) deletes its key wherever it was authored, unconditionally - a
      structural, bundle-wide change, independent of any row's value;
    - a column that is the final name of a rename chain (`renamed_from`
      maps it back to the original key) is likewise structural: every row
      that authored the *old* key gets the final value, independent of the
      trailing UPDATE's `WHERE` - a rename is a schema operation, not a row
      selection;
    - any other column still in the final schema (kept, or newly added via
      `ADD COLUMN`) compiles whenever that row's own value actually changed
      from what it was originally authored with - regardless of whether the
      trailing `UPDATE` selected that row - plus the one case a value
      comparison alone can't resolve: a `RETURNING`-selected row whose value
      was, and still is, NULL, which always means "delete this key." A row
      neither selected nor whose value changed is left exactly as authored.
    """
    diff: dict[str, str | None] = {}
    for name in field_names:
        if name in removed_columns:
            if name in concept.frontmatter:
                diff[name] = None
            continue
        if name in read_only_fields:
            continue
        origin = renamed_from.get(name)
        if origin is not None:
            # The new name never existed before this script, so there's no
            # existing value of *this* key to compare against - only
            # whether the old key was ever authored at all.
            entry = _compile_field_value(
                after_row.get(name), was_present=origin in concept.frontmatter
            )
        else:
            entry = _compile_changed_field_value(
                after_row.get(name),
                was_present=name in concept.frontmatter,
                current_value=concept.frontmatter.get(name),
                selected=selected,
            )
        if not isinstance(entry, _NotApplicable):
            diff[name] = entry
    return diff


def _execute_script(
    con: IbisConnection,
    materialized: _MaterializeResult,
    alter_queries: list[str],
    update_query: str,
) -> _ScriptOutcome:
    """Run the script transactionally and compile its result.

    Raises :class:`ApplyError` for any script failure or contract violation.
    Nothing about the real bundle is at risk at any point here: only the
    ephemeral in-memory database is touched.
    """
    snapshots = _snapshot_types(con, materialized.fields_by_type)
    selected_ids, renamed_by_type = _run_transaction(
        con,
        list(materialized.fields_by_type),
        alter_queries,
        update_query,
        materialized.declared_by_type,
        materialized.protected_by_type,
    )

    touched = _find_touched_type(con, snapshots)
    selected_type = _type_of_selected(materialized.concepts_by_type, selected_ids)
    if touched is None:
        touched = selected_type
    elif selected_type is not None and selected_type != touched:
        msg = f"script touched more than one type's table: {sorted({touched, selected_type})}"
        raise ApplyError(msg)
    if touched is None:
        return _ScriptOutcome(touched_type=None)

    renamed = renamed_by_type.get(touched, {})
    renamed_from = {new_name: old_name for old_name, new_name in renamed.items()}

    after_schema = _describe(con, touched)
    structured = materialized.structured_by_type[touched]
    protected = materialized.protected_by_type[touched]
    declared = materialized.declared_by_type[touched]
    _check_result_schema(after_schema, snapshots[touched].schema, structured, protected)

    _check_result_rows(snapshots[touched].table, con.table(touched), protected)

    after_rows = _fetch_rows(con, touched)
    field_names = materialized.fields_by_type[touched]
    all_field_names = sorted((set(after_schema) | set(field_names)) - protected)
    removed_columns = frozenset(snapshots[touched].schema) - set(after_schema) - protected
    row_diffs = tuple(
        _RowDiff(concept_id=concept.concept_id, changed_fields=diff)
        for concept in materialized.concepts_by_type[touched]
        for diff in (
            _compile_row_diff(
                concept,
                all_field_names,
                removed_columns,
                renamed_from,
                after_rows[concept.concept_id],
                declared,
                selected=concept.concept_id in selected_ids,
            ),
        )
        if diff
    )
    return _ScriptOutcome(touched_type=touched, row_diffs=row_diffs)


def _apply_frontmatter_changes(
    yaml: YAML, frontmatter_text: str, changes: dict[str, str | None]
) -> str:
    data = yaml.load(frontmatter_text)
    for key, value in changes.items():
        if value is None:
            if key in data:
                del data[key]
        else:
            data[key] = value
    buffer = StringIO()
    yaml.dump(data, buffer)
    return buffer.getvalue()


def _build_sugar_sql(type_name: str, field_name: str, from_value: str, to_value: str) -> str:
    escaped_to = to_value.replace("'", "''")
    escaped_from = from_value.replace("'", "''")
    quoted_field = _quote_ident(field_name)
    # Identifiers are quoted and string literals are '-doubled, not interpolated raw.
    return (
        f"UPDATE {_quote_ident(type_name)} SET {quoted_field} = '{escaped_to}' "
        f"WHERE {quoted_field} = '{escaped_from}'"
    )


def _snapshot_for_apply(root: Path, exclude: Sequence[str]) -> _BundleSnapshot:
    """Capture apply's coherent snapshot while preserving its public conflict wording."""
    try:
        return _snapshot_bundle(root, exclude)
    except WriteSupportError as exc:
        message = str(exc).replace(
            "file changed while it was being read",
            "file changed while apply was reading it",
        )
        raise ApplyError(message) from exc


def apply_bundle(  # each argument is an independent public CLI flag.
    path: str,
    *,
    sql: str | None = None,
    type_name: str | None = None,
    field_name: str | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
    write: bool = False,
    exclude: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, object]:
    """Mutate frontmatter fields across a bundle, per RFC 0005."""
    root = Path(path).resolve()
    if sql is None:
        sugar_complete = (
            type_name is not None
            and field_name is not None
            and from_value is not None
            and to_value is not None
        )
        if not sugar_complete:
            msg = "either --sql, or --type/--field/--from/--to together, are required"
            raise ApplyError(msg)
        sql = _build_sugar_sql(type_name, field_name, from_value, to_value)

    try:
        alter_queries, update_query = _parse_script(sql)

        # Captured first and together: the manifest baseline used for the
        # write-time conflict check comes from the exact same walk, and for
        # concept files the exact same bytes, that fed the SQL diff below -
        # not a second, later, independent filesystem visit.
        snapshot = _snapshot_for_apply(root, exclude)
        concepts = snapshot.concepts

        baseline = validate_path(root, exclude)
        baseline_keys = {
            (v.code, v.path, v.message) for v in baseline.violations if v.severity == Severity.ERROR
        }

        declarations = discover_declared_schemas(
            root,
            {concept.concept_type for concept in concepts},
            spec_template,
        )
        con = ibis.duckdb.connect()
        if declarations:
            con.raw_sql("SET TimeZone = 'UTC'")
        materialized = _materialize(con, concepts, declarations)
        outcome = _execute_script(con, materialized, alter_queries, update_query)
    except (ApplyError, DeclaredSchemaError, TypedTableError, WriteSupportError) as exc:
        return ApplyResult(succeeded=False, error=str(exc)).to_dict()

    if outcome.touched_type is None or not outcome.row_diffs:
        return ApplyResult().to_dict()

    concepts_by_id = {c.concept_id: c for c in concepts}
    content_hashes = {c.relative: c.content_hash for c in concepts}
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096

    changed_paths: list[str] = []
    skipped_paths: list[str] = []
    candidates: dict[str, tuple[_RawDocument, str, Path]] = {}
    for row in outcome.row_diffs:
        concept = concepts_by_id[row.concept_id]
        # Built from the snapshot's own bytes (`concept.raw`), not a fresh
        # read of the real path: the candidate must reflect exactly what the
        # SQL diff was computed against, not whatever happens to be on disk
        # by the time this loop runs.
        if not _round_trips_losslessly(yaml, concept.raw.frontmatter_text):
            skipped_paths.append(concept.relative)
            continue
        new_frontmatter_text = _apply_frontmatter_changes(
            yaml, concept.raw.frontmatter_text, row.changed_fields
        )
        candidates[concept.relative] = (concept.raw, new_frontmatter_text, concept.path)
        changed_paths.append(concept.relative)

    if skipped_paths:
        return ApplyResult(
            skipped_paths=tuple(sorted(skipped_paths)),
            succeeded=False,
            error="one or more matched documents cannot round-trip losslessly",
        ).to_dict()

    if not write:
        return ApplyResult(changed_paths=tuple(sorted(changed_paths))).to_dict()

    touched_hashes = {rel: content_hashes[rel] for rel in candidates}
    return _stage_validate_write(
        root,
        exclude,
        candidates,
        baseline_keys,
        changed_paths,
        touched_hashes,
        snapshot.manifest,
        conflict_error="the bundle changed since apply validated it",
        temp_prefix="okf-apply-",
    )
