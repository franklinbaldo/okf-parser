"""Relational writes to frontmatter fields via SQL, per RFC 0005.

`apply` materializes every concept type as its own DuckDB table (named for
the exact authored ``type`` value, quoted) inside one in-memory database,
then hands the caller's ``--sql`` to it: zero or more leading
``ALTER TABLE`` statements, followed by exactly one ``UPDATE``, run as a
single transaction. DuckDB's own parser, binder and catalog resolve every
identifier and every ALTER's effect; this module never re-derives "what did
the script mean to do." Instead, for whichever single type table the script
touched, every concept's target frontmatter is *compiled directly from the
final relational state* against the original document - a column absent (or
NULL) from the final row means the key is absent from the document, a column
present with a non-NULL value means the key holds exactly that value. The
same final state always compiles to the same document, regardless of how
many statements, or which ones, produced it.
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
from okf_parser.discovery import IGNORED_DIRECTORIES, is_markdown_filename
from okf_parser.exclusion import ExclusionRules
from okf_parser.models import Severity
from okf_parser.parser import (
    DocumentParseError,
    concept_id,
    is_reserved_document,
    parse_document_text,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

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
    frontmatter_text: str
    body: str
    content_hash: str


def _read_concept_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _parse_concept(root: Path, path: Path, raw: bytes) -> _Concept | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    normalized = text.removeprefix("﻿")
    try:
        parsed = parse_document_text(path, text)
    except DocumentParseError:
        return None
    if not parsed.concept_type:
        return None
    # Same pattern parser.py itself matches with; kept raw (not reformatted)
    # for the read-only __okf_frontmatter column, distinct from `frontmatter`
    # (the parsed mapping used to build the writable columns).
    match = _FRONTMATTER_RE.match(normalized)
    frontmatter_text = match.group(1) if match else ""
    return _Concept(
        path=path,
        relative=path.relative_to(root).as_posix(),
        concept_id=concept_id(root, path),
        concept_type=parsed.concept_type,
        frontmatter=dict(parsed.frontmatter),
        frontmatter_text=frontmatter_text,
        body=parsed.body,
        content_hash=hashlib.sha256(raw).hexdigest(),
    )


def _file_signature(path: Path) -> tuple[int, int]:
    """(size, mtime_ns) for one file - the freshness check `_snapshot_manifest` uses."""
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def _probe_file(path: Path) -> tuple[int, int]:
    """(size, mtime_ns) for one file - the read-consistency check `_snapshot_bundle` uses.

    Functionally identical to `_file_signature`; kept as a distinct name so
    the two independent freshness checks (bracketing a concept read here,
    versus the write-time recheck against the whole manifest) can be
    exercised in isolation from each other.
    """
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class _BundleSnapshot:
    manifest: dict[str, tuple[int, int]]
    concepts: list[_Concept]


def _snapshot_bundle(root: Path, exclude: Sequence[str]) -> _BundleSnapshot:
    """One coherent walk: every real file's freshness signature, concept bytes read once.

    Reading a concept document's bytes and recording its manifest signature
    in the very same visit - rather than as two separate filesystem passes
    made minutes apart in wall-clock terms - closes (to the extent a normal
    filesystem allows at all) the window where a concurrent edit between
    "read for the SQL diff" and "record the freshness baseline" would let a
    document be materialized against one version of its content while the
    later write-time conflict check compares against a manifest that was
    itself captured after the edit, and so never notices anything moved.
    """
    rules = ExclusionRules.read(root, exclude)
    prunes = not rules.has_negation
    manifest: dict[str, tuple[int, int]] = {}
    concepts: list[_Concept] = []
    for directory, directory_names, filenames in root.walk(follow_symlinks=False):
        base = directory.relative_to(root)
        directory_names[:] = [
            name
            for name in directory_names
            if name not in IGNORED_DIRECTORIES
            and not (directory / name).is_symlink()
            and not (prunes and rules.excludes((base / name).as_posix(), is_dir=True))
        ]
        for name in filenames:
            source = directory / name
            if source.is_symlink():
                continue
            posix = (base / name).as_posix()
            is_concept_candidate = (
                is_markdown_filename(name)
                and not rules.excludes(posix)
                and not is_reserved_document(source)
            )
            if not is_concept_candidate:
                manifest[posix] = _file_signature(source)
                continue
            # stat both sides of the read, not just after it: a same-size
            # replace landing between `read_bytes()` and a single trailing
            # `stat()` would record (old bytes' length, new mtime) - a
            # signature that happens to still match a same-size file at the
            # final recheck, so the stale read is never caught. Requiring
            # the two stats to agree means the recorded signature always
            # truly corresponds to the bytes just read, not to whichever
            # file existed at the moment of the second syscall.
            before_stat = _probe_file(source)
            raw = _read_concept_bytes(source)
            after_stat = _probe_file(source)
            if before_stat != after_stat:
                msg = f"file changed while apply was reading it: {posix}"
                raise ApplyError(msg)
            manifest[posix] = after_stat
            concept = _parse_concept(root, source, raw)
            if concept is not None:
                concepts.append(concept)
    return _BundleSnapshot(manifest=manifest, concepts=concepts)


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
        columns["__okf_frontmatter"].append(concept.frontmatter_text)
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
            pa.field("__okf_frontmatter", pa.string()),
            *(pa.field(name, pa.string()) for name in field_names),
        ]
    )
    # A local Python variable, referenced by name in the SQL string below:
    # DuckDB's replacement scan finds it via the calling frame, so nothing is
    # ever registered into the catalog under a predictable name a real
    # `type` could collide with (the `_before`-table problem, one level
    # earlier in the pipeline). Ruff can't see that reference inside the
    # f-string, hence the unused-variable suppression.
    okf_apply_stage_table = pa.table(columns, schema=schema)  # noqa: F841
    # table_name is quoted via _quote_ident, not interpolated raw.
    query = f"CREATE TEMP TABLE {_quote_ident(table_name)} AS SELECT * FROM okf_apply_stage_table"  # noqa: S608
    con.execute(query)


@dataclass(frozen=True, slots=True)
class _MaterializeResult:
    fields_by_type: dict[str, list[str]]
    concepts_by_type: dict[str, list[_Concept]]
    structured_by_type: dict[str, frozenset[str]]


def _materialize(
    con: duckdb.DuckDBPyConnection, concepts: Sequence[_Concept]
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
    for type_name, type_concepts in sorted(by_type.items()):
        kinds = _field_kinds(type_concepts)
        _check_reserved_field_names(type_name, kinds.scalar)
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


def _check_alter_shape(
    before: dict[str, dict[str, str]], after: dict[str, dict[str, str]], query: str
) -> None:
    """Reject any ALTER whose catalog delta isn't a single add/drop/rename column.

    The RFC promises leading statements are limited to
    ``ADD/DROP/RENAME COLUMN``; DuckDB's grammar allows other `ALTER TABLE`
    forms (a type change, a constraint, a default) that a name/type diff
    wouldn't otherwise notice, since `DESCRIBE` only reports name and type.
    Checked against the real catalog per statement, not by parsing the SQL.
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


def _run_transaction(
    con: duckdb.DuckDBPyConnection,
    type_names: Sequence[str],
    alter_queries: list[str],
    update_query: str,
) -> None:
    con.execute("BEGIN TRANSACTION")
    try:
        for query in alter_queries:
            before = {t: _describe(con, t) for t in type_names}
            con.execute(query)
            after = {t: _describe(con, t) for t in type_names}
            _check_alter_shape(before, after, query)
        con.execute(update_query)
    except duckdb.Error as exc:
        con.execute("ROLLBACK")
        msg = f"script failed: {exc}"
        raise ApplyError(msg) from exc
    except ApplyError:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def _find_touched_type(
    con: duckdb.DuckDBPyConnection, snapshots: dict[str, _TypeSnapshot]
) -> str | None:
    try:
        changed_types = [
            type_name
            for type_name, snapshot in snapshots.items()
            if _describe(con, type_name) != snapshot.schema
            or _fetch_rows(con, type_name) != snapshot.rows
        ]
    except duckdb.Error as exc:
        msg = f"script failed: {exc}"
        raise ApplyError(msg) from exc
    if len(changed_types) > 1:
        msg = f"script touched more than one type's table: {sorted(changed_types)}"
        raise ApplyError(msg)
    return changed_types[0] if changed_types else None


def _check_result_schema(
    after_schema: dict[str, str], before_schema: dict[str, str], structured: frozenset[str]
) -> None:
    after_cols = set(after_schema)
    before_cols = set(before_schema)
    missing_protected = _PROTECTED_COLUMNS - after_cols
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
    for column in after_cols - before_cols:
        # ASCII-case-insensitive, matching the authored-side reserved-prefix
        # check: DuckDB folds unquoted/quoted ASCII case, so "__OKF_custom"
        # is exactly as reserved as "__okf_custom".
        if column[: len(_OKF_PREFIX)].lower() == _OKF_PREFIX:
            msg = f'column "{column}" collides with the reserved __okf_ prefix'
            raise ApplyError(msg)
        if column in structured:
            msg = (
                f'column "{column}" reintroduces "{column}", which is structured '
                "(list/map) on at least one document of this type and is never "
                "a writable column"
            )
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


def _compile_row_diff(
    concept: _Concept,
    field_names: Sequence[str],
    removed_columns: frozenset[str],
    before_row: dict[str, object],
    after_row: dict[str, object],
) -> dict[str, str | None]:
    """Recompute one concept's target frontmatter purely from the final relation.

    Deliberately blind to *how* the final state was reached - a rename, a
    drop-then-recreate, a chain of renames, and a direct value update that
    happens to land on the same result all compile to the same diff, because
    only the relation's own before/after values are ever consulted, never the
    script's text. Two cases:

    - a column no longer in the final schema at all (dropped, or renamed
      away) deletes its key wherever it was authored, unconditionally - a
      structural, bundle-wide change, independent of any row's value;
    - a column still in the final schema (kept, or newly added) only
      touches a row whose *own* before/after value actually differs -
      `SET x = x` and a row an `UPDATE ... WHERE` never matched both leave
      that row's key exactly as authored, even if its value happens to be
      NULL. NULL still means absent per RFC 0005's contract, applied only
      to a value that is genuinely part of this change.
    """
    diff: dict[str, str | None] = {}
    for name in field_names:
        if name in removed_columns:
            if name in concept.frontmatter:
                diff[name] = None
            continue
        before_value = before_row.get(name)
        after_value = after_row.get(name)
        if before_value == after_value:
            continue
        target = after_value if isinstance(after_value, str) else None
        if target is None:
            if name in concept.frontmatter:
                diff[name] = None
        else:
            diff[name] = target
    return diff


def _execute_script(
    con: duckdb.DuckDBPyConnection,
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
    _run_transaction(con, list(materialized.fields_by_type), alter_queries, update_query)

    touched = _find_touched_type(con, snapshots)
    if touched is None:
        return _ScriptOutcome(touched_type=None)

    after_schema = _describe(con, touched)
    structured = materialized.structured_by_type[touched]
    _check_result_schema(after_schema, snapshots[touched].schema, structured)

    before_rows = snapshots[touched].rows
    after_rows = _fetch_rows(con, touched)
    _check_result_rows(before_rows, after_rows)

    field_names = materialized.fields_by_type[touched]
    all_field_names = sorted((set(after_schema) | set(field_names)) - _PROTECTED_COLUMNS)
    removed_columns = frozenset(snapshots[touched].schema) - set(after_schema) - _PROTECTED_COLUMNS
    row_diffs = tuple(
        _RowDiff(concept_id=concept.concept_id, changed_fields=diff)
        for concept in materialized.concepts_by_type[touched]
        for diff in (
            _compile_row_diff(
                concept,
                all_field_names,
                removed_columns,
                before_rows[concept.concept_id],
                after_rows[concept.concept_id],
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
        alter_queries, update_query = _parse_script(sql)

        # Captured first and together: the manifest baseline used for the
        # write-time conflict check comes from the exact same walk, and for
        # concept files the exact same bytes, that fed the SQL diff below -
        # not a second, later, independent filesystem visit.
        snapshot = _snapshot_bundle(root, exclude)
        concepts = snapshot.concepts

        baseline = validate_path(root, exclude)
        baseline_keys = {
            (v.code, v.path, v.message) for v in baseline.violations if v.severity == Severity.ERROR
        }

        con = duckdb.connect()
        materialized = _materialize(con, concepts)
        outcome = _execute_script(con, materialized, alter_queries, update_query)
    except ApplyError as exc:
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

    touched_hashes = {rel: content_hashes[rel] for rel in candidates}
    return _stage_validate_write(
        root, exclude, candidates, baseline_keys, changed_paths, touched_hashes, snapshot.manifest
    )


def _snapshot_manifest(root: Path) -> dict[str, tuple[int, int]]:
    """Relative path -> (size, mtime_ns) for every real file `check` could see.

    Pruned the same way `discover_markdown` prunes: ignored directories and
    symlinks are skipped, not walked - keeping this manifest and the
    candidate tree built from it looking at the same universe of files. Used
    only for the write-time recheck; the baseline comes from `_snapshot_bundle`.
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
    baseline_manifest: dict[str, tuple[int, int]],
) -> dict[str, object]:
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
