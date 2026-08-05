"""Relational writes to frontmatter fields via SQL, per RFC 0005.

`apply` materializes every concept type as its own DuckDB table (named for
the exact authored ``type`` value, quoted) inside one in-memory database,
then hands the caller's ``--sql`` to it: zero or more leading
``ALTER TABLE ADD/DROP/RENAME COLUMN`` statements, followed by exactly one
``UPDATE``, run as a single transaction. DuckDB's own parser and binder
resolve every identifier; this module never re-derives "which type does
this string mean" itself. What changed is discovered from a before/after
diff of every table, validated, staged through a round-trip YAML writer,
checked against the bundle's own baseline, and only then replaces real
files.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pyarrow as pa
from ruamel.yaml import YAML

from okf_parser.bundle import validate_path
from okf_parser.discovery import IGNORED_DIRECTORIES, discover_markdown
from okf_parser.exclusion import ExclusionRules
from okf_parser.models import Severity
from okf_parser.parser import DocumentParseError, concept_id, is_reserved_document, parse_document

if TYPE_CHECKING:
    from collections.abc import Sequence

_OKF_PREFIX = "__okf_"
_IDENTITY_COLUMNS = ("__okf_path", "__okf_concept_id", "__okf_logical_key")
_BODY_COLUMNS = ("__okf_body", "__okf_body_lines")
_PROTECTED_COLUMNS = frozenset({*_IDENTITY_COLUMNS, *_BODY_COLUMNS})
_IDENT = r'(?:"(?:[^"]|"")+"|\w+)'
_ALTER_ADD_RE = re.compile(r"^\s*ALTER\s+TABLE\s+.+?\s+ADD\s+(?:COLUMN\s+)?", re.IGNORECASE)
_ALTER_DROP_RE = re.compile(
    rf"^\s*ALTER\s+TABLE\s+.+?\s+DROP\s+(?:COLUMN\s+)?(?P<column>{_IDENT})",
    re.IGNORECASE,
)
_ALTER_RENAME_COLUMN_RE = re.compile(
    rf"^\s*ALTER\s+TABLE\s+.+?\s+RENAME\s+COLUMN\s+(?P<from>{_IDENT})\s+TO\s+(?P<to>{_IDENT})",
    re.IGNORECASE,
)
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(.*?)(?:\r?\n)?---[ \t]*(?:\r?\n(.*))?\Z",
    re.DOTALL,
)
_ADD_COLUMN_TYPE_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+.+?\s+ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r'(?:"(?:[^"]|"")+"|\w+)\s+(?P<type>\w+)',
    re.IGNORECASE,
)


def _unquote_ident(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('""', '"')
    return raw


class ApplyError(ValueError):
    """Raised when `--sql` is malformed or a script violates the v1 contract."""


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """The outcome of one `apply` invocation."""

    changed_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    succeeded: bool = True
    written: bool = False
    validation: tuple[dict[str, object], ...] = ()
    conflict_paths: tuple[str, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """JSON-ready payload matching the CLI surface."""
        payload: dict[str, object] = {
            "changed_paths": list(self.changed_paths),
            "skipped_paths": list(self.skipped_paths),
            "succeeded": self.succeeded,
            "written": self.written,
            "validation": list(self.validation),
            "conflict_paths": list(self.conflict_paths),
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True, slots=True)
class _RawDocument:
    """A concept document's bytes, split for lossless round-tripping."""

    bom: bytes
    crlf: bool
    frontmatter_text: str
    body_text: str


def _read_raw(path: Path) -> _RawDocument | None:
    raw = path.read_bytes()
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    try:
        text = raw[len(bom) :].decode("utf-8")
    except UnicodeDecodeError:
        return None
    crlf = "\r\n" in text
    normalized = text.replace("\r\n", "\n")
    match = _FRONTMATTER_RE.match(normalized)
    if match is None:
        return None
    return _RawDocument(
        bom=bom, crlf=crlf, frontmatter_text=match.group(1), body_text=match.group(2) or ""
    )


def _round_trips_losslessly(yaml: YAML, frontmatter_text: str) -> bool:
    try:
        data = yaml.load(frontmatter_text)
        buffer = StringIO()
        yaml.dump(data, buffer)
    except Exception:  # noqa: BLE001 - any load/dump failure means "cannot round-trip"
        return False
    return buffer.getvalue().rstrip("\n") == frontmatter_text.rstrip("\n")


def _write_raw(path: Path, raw: _RawDocument, frontmatter_text: str) -> None:
    text = "---\n" + frontmatter_text
    if not text.endswith("\n"):
        text += "\n"
    text += "---\n" + raw.body_text
    if raw.crlf:
        text = text.replace("\n", "\r\n")
    data = raw.bom + text.encode("utf-8")
    tmp = path.with_name(f".{path.name}.okf-apply.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class _Concept:
    path: Path
    relative: str
    concept_id: str
    concept_type: str
    frontmatter: dict[str, object]
    body: str


def _load_concepts(root: Path, exclude: Sequence[str]) -> list[_Concept]:
    paths = discover_markdown(root, ExclusionRules.read(root, exclude))
    concepts: list[_Concept] = []
    for path in paths:
        if is_reserved_document(path):
            continue
        try:
            parsed = parse_document(path)
        except DocumentParseError:
            continue
        if not parsed.concept_type:
            continue
        concepts.append(
            _Concept(
                path=path,
                relative=path.relative_to(root).as_posix(),
                concept_id=concept_id(root, path),
                concept_type=parsed.concept_type,
                frontmatter=dict(parsed.frontmatter),
                body=parsed.body,
            )
        )
    return concepts


def _scalar_field_names(concepts: Sequence[_Concept]) -> list[str]:
    """Top-level authored keys that are scalar on every document that has them.

    A key observed as a list or map on even one document is excluded from the
    writable namespace entirely, on every document of that type - not just
    NULLed out on the documents where it's structured - so a value that would
    otherwise be silently overwritten or dropped never becomes reachable from
    SQL in the first place.
    """
    scalar_keys: set[str] = set()
    structured_keys: set[str] = set()
    for concept in concepts:
        for key, value in concept.frontmatter.items():
            if isinstance(value, str) or value is None:
                scalar_keys.add(key)
            else:
                structured_keys.add(key)
    return sorted(scalar_keys - structured_keys)


def _check_reserved_and_collisions(type_name: str, field_names: Sequence[str]) -> None:
    for name in field_names:
        if name.startswith(_OKF_PREFIX):
            msg = (
                f'type "{type_name}" has an authored field colliding with the '
                f"reserved __okf_ prefix: {name}"
            )
            raise ApplyError(msg)
    # DuckDB folds unquoted and quoted identifiers alike for ASCII case only,
    # not full Unicode casefolding (which conflates codepoints DuckDB itself
    # treats as distinct, e.g. "ß" vs "ss") - .lower() matches that.
    folded: dict[str, str] = {}
    for name in field_names:
        key = name.lower()
        if key in folded:
            msg = (
                f'type "{type_name}" has fields that collide under case-insensitive '
                f'identifier equality: "{folded[key]}" and "{name}"'
            )
            raise ApplyError(msg)
        folded[key] = name


def _build_table(
    con: duckdb.DuckDBPyConnection,
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
    }
    for name in field_names:
        columns[name] = []
    for concept in concepts:
        columns["__okf_path"].append(concept.relative)
        columns["__okf_concept_id"].append(concept.concept_id)
        columns["__okf_logical_key"].append(concept.concept_id)
        columns["__okf_body"].append(concept.body)
        columns["__okf_body_lines"].append(concept.body.splitlines())
        for name in field_names:
            value = concept.frontmatter.get(name)
            columns[name].append(value if isinstance(value, str) else None)

    schema = pa.schema(
        [
            pa.field("__okf_path", pa.string()),
            pa.field("__okf_concept_id", pa.string()),
            pa.field("__okf_logical_key", pa.string()),
            pa.field("__okf_body", pa.string()),
            pa.field("__okf_body_lines", pa.list_(pa.string())),
            *(pa.field(name, pa.string()) for name in field_names),
        ]
    )
    table = pa.table(columns, schema=schema)
    con.register("__okf_stage", table)
    try:
        # table_name is quoted via _quote_ident, not interpolated raw.
        query = f"CREATE TEMP TABLE {_quote_ident(table_name)} AS SELECT * FROM __okf_stage"  # noqa: S608
        con.execute(query)
    finally:
        con.unregister("__okf_stage")


def _materialize(
    con: duckdb.DuckDBPyConnection, concepts: Sequence[_Concept]
) -> tuple[dict[str, list[str]], dict[str, list[_Concept]]]:
    """Build one table per type inside the script's catalog; return its field names.

    No pre-mutation snapshot is created as a table here: a real `type` could be
    authored with a name that collides with an internal snapshot table, and
    any table in this catalog is addressable by the caller's own `--sql`. The
    pre-image instead lives entirely in Python (see `_snapshot_type`), never
    exposed to the script.
    """
    by_type: dict[str, list[_Concept]] = {}
    for concept in concepts:
        by_type.setdefault(concept.concept_type, []).append(concept)

    fields_by_type: dict[str, list[str]] = {}
    for type_name, type_concepts in sorted(by_type.items()):
        field_names = _scalar_field_names(type_concepts)
        _check_reserved_and_collisions(type_name, field_names)
        try:
            _build_table(con, type_name, field_names, type_concepts)
        except duckdb.CatalogException as exc:
            msg = (
                f'type "{type_name}" collides with another type under DuckDB '
                f"identifier equality: {exc}"
            )
            raise ApplyError(msg) from exc
        fields_by_type[type_name] = field_names
    return fields_by_type, by_type


@dataclass(frozen=True, slots=True)
class _TypeSnapshot:
    schema: dict[str, str]
    rows: dict[str, dict[str, object]]


def _snapshot_types(
    con: duckdb.DuckDBPyConnection, fields_by_type: dict[str, list[str]]
) -> dict[str, _TypeSnapshot]:
    """Capture every type table's schema and rows before the script runs.

    Held only in Python, never as a queryable table in `con`'s catalog, so the
    caller's `--sql` cannot address, corrupt, or collide a type against it.
    """
    return {
        type_name: _TypeSnapshot(schema=_describe(con, type_name), rows=_fetch_rows(con, type_name))
        for type_name in fields_by_type
    }


@dataclass(frozen=True, slots=True)
class _AlterEffect:
    """A schema-level ALTER's effect on a document's presence of a key."""

    action: str  # "drop" or "rename"
    column: str
    renamed_to: str | None = None


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


def _parse_alter_statement(statement: duckdb.Statement) -> tuple[str, _AlterEffect | None]:
    query = statement.query.strip().rstrip(";")
    if not str(statement.type).endswith("ALTER"):
        msg = (
            "--sql may only contain leading ALTER TABLE statements before "
            f"the final UPDATE, found: {query}"
        )
        raise ApplyError(msg)
    is_add = _ALTER_ADD_RE.match(query) is not None
    drop_match = _ALTER_DROP_RE.match(query)
    rename_match = _ALTER_RENAME_COLUMN_RE.match(query)
    if not (is_add or drop_match or rename_match):
        msg = (
            "--sql's leading statements must be ALTER TABLE ADD COLUMN, "
            f"DROP COLUMN, or RENAME COLUMN, found: {query}"
        )
        raise ApplyError(msg)
    if is_add:
        type_match = _ADD_COLUMN_TYPE_RE.match(query)
        column_type = type_match.group("type").upper() if type_match else ""
        if column_type != "VARCHAR":
            msg = f"ALTER TABLE ADD COLUMN must declare VARCHAR, found: {query}"
            raise ApplyError(msg)
        return query, None
    if drop_match:
        return query, _AlterEffect(action="drop", column=_unquote_ident(drop_match.group("column")))
    assert rename_match is not None  # noqa: S101 - the earlier guard leaves only this case.
    return query, _AlterEffect(
        action="rename",
        column=_unquote_ident(rename_match.group("from")),
        renamed_to=_unquote_ident(rename_match.group("to")),
    )


def _parse_script(sql: str) -> tuple[list[str], str, list[_AlterEffect]]:
    *leading, trailing = _extract_statements(sql)
    if not str(trailing.type).endswith("UPDATE"):
        msg = "--sql must end with exactly one UPDATE statement"
        raise ApplyError(msg)

    alter_queries: list[str] = []
    alter_effects: list[_AlterEffect] = []
    for statement in leading:
        query, effect = _parse_alter_statement(statement)
        alter_queries.append(query)
        if effect is not None:
            alter_effects.append(effect)

    update_query = trailing.query.strip().rstrip(";")
    return alter_queries, update_query, alter_effects


class _WriteNull:
    """Sentinel: write a literal YAML null, as opposed to deleting the key.

    Plain ``None`` means delete (RFC 0005's ``SET field = NULL`` contract).
    A `RENAME COLUMN` on a field that was already authored as an explicit
    YAML null must carry that null across under its new name, not delete it
    - these need to be distinguishable in a diff that otherwise only ever
    carries ``str | None``.
    """


_WRITE_NULL = _WriteNull()


@dataclass(frozen=True, slots=True)
class _RowDiff:
    concept_id: str
    changed_fields: dict[str, str | None | _WriteNull]


@dataclass(frozen=True, slots=True)
class _ScriptOutcome:
    touched_type: str | None
    row_diffs: tuple[_RowDiff, ...] = ()


def _describe(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    rows = con.execute(f"DESCRIBE {_quote_ident(table)}").fetchall()
    return {row[0]: row[1] for row in rows}


def _as_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _fetch_rows(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, dict[str, object]]:
    # table is quoted via _quote_ident, not interpolated raw.
    query = f"SELECT * FROM {_quote_ident(table)}"  # noqa: S608
    cursor = con.execute(query)
    columns = [d[0] for d in cursor.description]
    rows: dict[str, dict[str, object]] = {}
    for record in cursor.fetchall():
        row = dict(zip(columns, record, strict=True))
        rows[row["__okf_concept_id"]] = row
    return rows


def _run_transaction(
    con: duckdb.DuckDBPyConnection, alter_queries: list[str], update_query: str
) -> None:
    con.execute("BEGIN TRANSACTION")
    try:
        for query in alter_queries:
            con.execute(query)
        con.execute(update_query)
    except duckdb.Error as exc:
        con.execute("ROLLBACK")
        msg = f"script failed: {exc}"
        raise ApplyError(msg) from exc
    con.execute("COMMIT")


def _find_touched_type(
    con: duckdb.DuckDBPyConnection, snapshots: dict[str, _TypeSnapshot]
) -> str | None:
    changed_types = [
        type_name
        for type_name, snapshot in snapshots.items()
        if _describe(con, type_name) != snapshot.schema
        or _fetch_rows(con, type_name) != snapshot.rows
    ]
    if len(changed_types) > 1:
        msg = f"script touched more than one type's table: {sorted(changed_types)}"
        raise ApplyError(msg)
    return changed_types[0] if changed_types else None


def _check_result_schema(after_schema: dict[str, str], before_cols: set[str]) -> None:
    after_cols = set(after_schema)
    missing_protected = _PROTECTED_COLUMNS - after_cols
    if missing_protected:
        msg = f"script removed protected columns: {sorted(missing_protected)}"
        raise ApplyError(msg)
    for column in after_cols - before_cols:
        if column.startswith(_OKF_PREFIX):
            msg = f'column "{column}" collides with the reserved __okf_ prefix'
            raise ApplyError(msg)
        if after_schema[column].upper() != "VARCHAR":
            msg = f'column "{column}" must be VARCHAR, got {after_schema[column]}'
            raise ApplyError(msg)


def _check_result_rows(
    before_rows: dict[str, dict[str, object]], after_rows: dict[str, dict[str, object]]
) -> None:
    if before_rows.keys() != after_rows.keys():
        msg = "script changed row identity or cardinality"
        raise ApplyError(msg)
    for row_id, before_row in before_rows.items():
        after_row = after_rows[row_id]
        for column in _PROTECTED_COLUMNS:
            if before_row.get(column) != after_row.get(column):
                msg = f'row "{row_id}" changed protected column "{column}"'
                raise ApplyError(msg)


def _presence_forced_changes(
    alter_effects: Sequence[_AlterEffect],
    touched_concepts: Sequence[_Concept],
    after_rows: dict[str, dict[str, object]],
) -> dict[str, dict[str, str | None | _WriteNull]]:
    """Force DROP/RENAME COLUMN to touch every doc where the key was present.

    Includes an authored explicit YAML null. A value-only diff cannot see
    this: an absent key and an authored
    ``field: null`` both read back as SQL NULL, so if a column being dropped
    or renamed held NULL on every row, before == after for that column and no
    row diff would otherwise be produced - leaving the old key behind. A
    rename additionally must carry a null value across as a null, not a
    deletion - `_WRITE_NULL` marks that case.
    """
    forced: dict[str, dict[str, str | None | _WriteNull]] = {}
    for effect in alter_effects:
        for concept in touched_concepts:
            if effect.column not in concept.frontmatter:
                continue
            entry = forced.setdefault(concept.concept_id, {})
            entry[effect.column] = None
            if effect.action == "rename" and effect.renamed_to is not None:
                after_row = after_rows.get(concept.concept_id, {})
                value = after_row.get(effect.renamed_to)
                entry[effect.renamed_to] = value if isinstance(value, str) else _WRITE_NULL
    return forced


def _execute_script(  # noqa: PLR0913 - each argument is a distinct execution input.
    con: duckdb.DuckDBPyConnection,
    fields_by_type: dict[str, list[str]],
    concepts_by_type: dict[str, list[_Concept]],
    alter_queries: list[str],
    alter_effects: list[_AlterEffect],
    update_query: str,
) -> _ScriptOutcome:
    """Run the script transactionally and validate its result.

    Raises :class:`ApplyError` for any script failure or contract violation.
    Nothing about the real bundle is at risk at any point here: only the
    ephemeral in-memory database is touched.
    """
    snapshots = _snapshot_types(con, fields_by_type)
    _run_transaction(con, alter_queries, update_query)

    touched = _find_touched_type(con, snapshots)
    if touched is None:
        return _ScriptOutcome(touched_type=None)

    before_cols = {*_PROTECTED_COLUMNS, *fields_by_type[touched]}
    after_schema = _describe(con, touched)
    _check_result_schema(after_schema, before_cols)

    before_rows = snapshots[touched].rows
    after_rows = _fetch_rows(con, touched)
    _check_result_rows(before_rows, after_rows)

    # The union of before- and after-authored fields, not just after: a column
    # dropped or renamed away must still be visited so its old key is deleted
    # from frontmatter, even though it is no longer part of the after schema.
    field_names = sorted((set(after_schema) | set(fields_by_type[touched])) - _PROTECTED_COLUMNS)
    changed_by_id: dict[str, dict[str, str | None | _WriteNull]] = {}
    for row_id, before_row in before_rows.items():
        after_row = after_rows[row_id]
        diffs: dict[str, str | None | _WriteNull] = {
            name: _as_text(after_row.get(name))
            for name in field_names
            if before_row.get(name) != after_row.get(name)
        }
        if diffs:
            changed_by_id[row_id] = diffs

    forced = _presence_forced_changes(alter_effects, concepts_by_type[touched], after_rows)
    for row_id, forced_fields in forced.items():
        changed_by_id.setdefault(row_id, {}).update(forced_fields)

    row_diffs = tuple(
        _RowDiff(concept_id=row_id, changed_fields=changed_fields)
        for row_id, changed_fields in changed_by_id.items()
    )
    return _ScriptOutcome(touched_type=touched, row_diffs=row_diffs)


def _apply_frontmatter_changes(
    yaml: YAML, frontmatter_text: str, changes: dict[str, str | None | _WriteNull]
) -> str:
    data = yaml.load(frontmatter_text)
    for key, value in changes.items():
        if value is None:
            if key in data:
                del data[key]
        elif isinstance(value, _WriteNull):
            data[key] = None
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
        f"UPDATE {_quote_ident(type_name)} SET {quoted_field} = '{escaped_to}' "  # noqa: S608
        f"WHERE {quoted_field} = '{escaped_from}'"
    )


def apply_bundle(  # noqa: PLR0913 - each argument is an independent public CLI flag.
    path: str,
    *,
    sql: str | None = None,
    type_name: str | None = None,
    field_name: str | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
    write: bool = False,
    exclude: Sequence[str] = (),
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
        alter_queries, update_query, alter_effects = _parse_script(sql)

        baseline = validate_path(root, exclude)
        baseline_keys = {
            (v.code, v.path, v.message) for v in baseline.violations if v.severity == Severity.ERROR
        }

        concepts = _load_concepts(root, exclude)
        # Hashed as of the same read that fed the SQL diff, not after: a file
        # edited between this point and the later re-read for round-trip
        # writeback must still be caught as a conflict, not silently
        # overwritten with a diff computed against stale content.
        read_hashes = {concept.relative: _sha256(concept.path) for concept in concepts}
        con = duckdb.connect()
        fields_by_type, concepts_by_type = _materialize(con, concepts)
        outcome = _execute_script(
            con, fields_by_type, concepts_by_type, alter_queries, alter_effects, update_query
        )
    except ApplyError as exc:
        return ApplyResult(succeeded=False, error=str(exc)).to_dict()

    if outcome.touched_type is None or not outcome.row_diffs:
        return ApplyResult().to_dict()

    concepts_by_id = {c.concept_id: c for c in concepts}
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096

    changed_paths: list[str] = []
    skipped_paths: list[str] = []
    candidates: dict[str, tuple[_RawDocument, str, Path]] = {}
    for row in outcome.row_diffs:
        concept = concepts_by_id[row.concept_id]
        raw = _read_raw(concept.path)
        if raw is None or not _round_trips_losslessly(yaml, raw.frontmatter_text):
            skipped_paths.append(concept.relative)
            continue
        new_frontmatter_text = _apply_frontmatter_changes(
            yaml, raw.frontmatter_text, row.changed_fields
        )
        candidates[concept.relative] = (raw, new_frontmatter_text, concept.path)
        changed_paths.append(concept.relative)

    if skipped_paths:
        return ApplyResult(
            skipped_paths=tuple(sorted(skipped_paths)),
            succeeded=False,
            error="one or more matched documents cannot round-trip losslessly",
        ).to_dict()

    if not write:
        return ApplyResult(changed_paths=tuple(sorted(changed_paths))).to_dict()

    touched_hashes = {rel: read_hashes[rel] for rel in candidates}
    return _stage_validate_write(
        root, exclude, candidates, baseline_keys, changed_paths, touched_hashes
    )


def _file_signature(path: Path) -> tuple[int, int]:
    """(size, mtime_ns) for one file - the freshness check `_snapshot_manifest` uses."""
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def _snapshot_manifest(root: Path) -> dict[str, tuple[int, int]]:
    """Relative path -> (size, mtime_ns) for every real file `check` could see.

    Pruned the same way `discover_markdown` prunes: ignored directories and
    symlinks are skipped, not walked - keeping this manifest and the
    candidate tree built from it looking at the same universe of files.
    """
    manifest: dict[str, tuple[int, int]] = {}
    for directory, directory_names, filenames in root.walk(follow_symlinks=False):
        base = directory.relative_to(root)
        directory_names[:] = [
            name
            for name in directory_names
            if name not in IGNORED_DIRECTORIES and not (directory / name).is_symlink()
        ]
        for name in filenames:
            source = directory / name
            if source.is_symlink():
                continue
            manifest[(base / name).as_posix()] = _file_signature(source)
    return manifest


def _stage_validate_write(  # noqa: PLR0913 - each argument is a distinct write-path input.
    root: Path,
    exclude: Sequence[str],
    candidates: dict[str, tuple[_RawDocument, str, Path]],
    baseline_keys: set[tuple[str, str, str]],
    changed_paths: list[str],
    touched_hashes: dict[str, str],
) -> dict[str, object]:
    baseline_manifest = _snapshot_manifest(root)

    with tempfile.TemporaryDirectory(prefix="okf-apply-") as tmp:
        candidate_root = Path(tmp) / "bundle"
        candidate_root.mkdir()
        try:
            _build_candidate_tree(root, candidate_root, candidates)
        except OSError as exc:
            return ApplyResult(
                succeeded=False, error=f"could not stage the candidate bundle: {exc}"
            ).to_dict()

        candidate_report = validate_path(candidate_root, exclude)
        candidate_keys = {
            (v.code, v.path, v.message)
            for v in candidate_report.violations
            if v.severity == Severity.ERROR
        }
        new_diagnostics = candidate_keys - baseline_keys
        if new_diagnostics:
            validation_payload: tuple[dict[str, object], ...] = tuple(
                {"code": code, "path": p, "message": message}
                for code, p, message in sorted(new_diagnostics)
            )
            return ApplyResult(
                succeeded=False,
                validation=validation_payload,
                error="candidate bundle introduces new normative diagnostics",
            ).to_dict()

        # Re-check the whole manifest `check` saw, immediately before writing,
        # not just the touched documents: an untouched file may have changed,
        # and a file may have appeared or disappeared since validation ran.
        current_manifest = _snapshot_manifest(root)
        added_or_removed = set(baseline_manifest) ^ set(current_manifest)
        stale_untouched = {
            rel
            for rel in baseline_manifest.keys() & current_manifest.keys() - candidates.keys()
            if baseline_manifest[rel] != current_manifest[rel]
        }
        stale_touched = {
            rel for rel, (_, _, real) in candidates.items() if _sha256(real) != touched_hashes[rel]
        }
        conflicts = added_or_removed | stale_untouched | stale_touched
        if conflicts:
            return ApplyResult(
                succeeded=False,
                conflict_paths=tuple(sorted(conflicts)),
                error="the bundle changed since apply validated it",
            ).to_dict()

        for raw, frontmatter_text, real_path in candidates.values():
            _write_raw(real_path, raw, frontmatter_text)

    return ApplyResult(changed_paths=tuple(sorted(changed_paths)), written=True).to_dict()


def _build_candidate_tree(
    root: Path, candidate_root: Path, candidates: dict[str, tuple[_RawDocument, str, Path]]
) -> None:
    """Hardlink (copy-fallback) every real file `check` would see into a staging tree.

    Pruned like `_snapshot_manifest`/`discover_markdown`: ignored directories
    (``.git``, ``.venv``, caches) and symlinks are skipped, not staged, so a
    write doesn't multiply I/O across an unrelated VCS or virtualenv tree for
    no validation benefit.
    """
    for directory, directory_names, filenames in root.walk(follow_symlinks=False):
        base = directory.relative_to(root)
        directory_names[:] = [
            name
            for name in directory_names
            if name not in IGNORED_DIRECTORIES and not (directory / name).is_symlink()
        ]
        for name in filenames:
            source = directory / name
            if source.is_symlink():
                continue
            relative = base / name
            destination = candidate_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            posix = relative.as_posix()
            if posix in candidates:
                raw, frontmatter_text, _ = candidates[posix]
                _write_raw(destination, raw, frontmatter_text)
                continue
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)
