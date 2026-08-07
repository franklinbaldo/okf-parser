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
import ibis
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


def _parse_raw(data: bytes) -> _RawDocument | None:
    """Split already-read bytes for lossless round-tripping.

    Deliberately takes bytes, not a path: every candidate this module writes
    must be built from the same bytes the SQL diff was computed against
    (captured once in `_snapshot_bundle`), never from a fresh read of the
    real file made later in the pipeline - that second, later read is
    exactly the gap a concurrent edit could hide in.
    """
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    try:
        text = data[len(bom) :].decode("utf-8")
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
    except Exception:  # any load/dump failure means "cannot round-trip"
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
    content_hash: str
    raw: _RawDocument


def _parse_concept(root: Path, path: Path, raw: bytes) -> _Concept | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        parsed = parse_document_text(path, text)
    except DocumentParseError:
        return None
    if not parsed.concept_type:
        return None
    raw_document = _parse_raw(raw)
    if raw_document is None:
        return None
    return _Concept(
        path=path,
        relative=path.relative_to(root).as_posix(),
        concept_id=concept_id(root, path),
        concept_type=parsed.concept_type,
        frontmatter=dict(parsed.frontmatter),
        body=parsed.body,
        content_hash=hashlib.sha256(raw).hexdigest(),
        raw=raw_document,
    )


def _file_signature(path: Path) -> tuple[int, int]:
    """(size, mtime_ns) for one file - a freshness baseline, or to bracket a read."""
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def _prune_walk_directories(
    directory: Path, directory_names: list[str], base: Path, rules: ExclusionRules, *, prunes: bool
) -> None:
    """Filter `directory_names` in place to the exact universe every bundle walk must agree on.

    Shared by `_snapshot_bundle` (the baseline), `_snapshot_manifest` (the
    write-time recheck), and `_build_candidate_tree` (the staged tree
    `check` validates) - if any of the three pruned a different set of
    directories, an excluded directory's files would appear in one walk's
    universe but not another's, and `apply --write` would either miss a real
    conflict or report a false one purely from directory-level exclusion.
    """
    directory_names[:] = [
        name
        for name in directory_names
        if name not in IGNORED_DIRECTORIES
        and not (directory / name).is_symlink()
        and not (prunes and rules.excludes((base / name).as_posix(), is_dir=True))
    ]


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
        _prune_walk_directories(directory, directory_names, base, rules, prunes=prunes)
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
            before_stat = _file_signature(source)
            raw = source.read_bytes()
            after_stat = _file_signature(source)
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


def _materialize(con: IbisConnection, concepts: Sequence[_Concept]) -> _MaterializeResult:
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
            table=ibis.memtable(con.table(type_name).execute()),
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
    before: dict[str, dict[str, str]], after: dict[str, dict[str, str]], query: str
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
    if is_rename:
        (old_name,) = removed
        (new_name,) = added
        return (type_name, old_name, new_name)
    return None


def _run_transaction(
    con: IbisConnection,
    type_names: Sequence[str],
    alter_queries: list[str],
    update_query: str,
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
            rename = _check_alter_shape(before, after, query)
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


def _check_result_rows(before: ibis.Table, after: ibis.Table) -> None:
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
    for column in sorted(_PROTECTED_COLUMNS):
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
        con, list(materialized.fields_by_type), alter_queries, update_query
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
    _check_result_schema(after_schema, snapshots[touched].schema, structured)

    _check_result_rows(snapshots[touched].table, con.table(touched))

    after_rows = _fetch_rows(con, touched)
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
                renamed_from,
                after_rows[concept.concept_id],
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

        con = ibis.duckdb.connect()
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
        root, exclude, candidates, baseline_keys, changed_paths, touched_hashes, snapshot.manifest
    )


def _snapshot_manifest(root: Path, exclude: Sequence[str]) -> dict[str, tuple[int, int]]:
    """Relative path -> (size, mtime_ns) for every real file `check` could see.

    Pruned via the same `_prune_walk_directories` helper - and the same
    `exclude` argument - `_snapshot_bundle` uses for the baseline: an
    excluded directory must be absent from both walks' universes, or present
    in both, never split between them, or a mismatch shows up as a false
    conflict (or a missed real one) purely from directory-level exclusion.
    Used only for the write-time recheck; the baseline comes from
    `_snapshot_bundle`.
    """
    rules = ExclusionRules.read(root, exclude)
    prunes = not rules.has_negation
    manifest: dict[str, tuple[int, int]] = {}
    for directory, directory_names, filenames in root.walk(follow_symlinks=False):
        base = directory.relative_to(root)
        _prune_walk_directories(directory, directory_names, base, rules, prunes=prunes)
        for name in filenames:
            source = directory / name
            if source.is_symlink():
                continue
            manifest[(base / name).as_posix()] = _file_signature(source)
    return manifest


def _stage_validate_write(  # each argument is a distinct write-path input.
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
            _build_candidate_tree(root, candidate_root, candidates, exclude)
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
        current_manifest = _snapshot_manifest(root, exclude)
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
    root: Path,
    candidate_root: Path,
    candidates: dict[str, tuple[_RawDocument, str, Path]],
    exclude: Sequence[str],
) -> None:
    """Hardlink (copy-fallback) every real file `check` would see into a staging tree.

    Pruned via the same `_prune_walk_directories` helper `_snapshot_bundle`
    and `_snapshot_manifest` use, with the same `exclude` argument: an
    excluded directory (``.okfignore``, `--exclude`) is skipped exactly the
    same way here as in the manifest walks, not just the fixed
    `IGNORED_DIRECTORIES` (``.git``, ``.venv``, caches) - keeping the staged
    tree's universe identical to what both manifests already agree on.
    """
    rules = ExclusionRules.read(root, exclude)
    prunes = not rules.has_negation
    for directory, directory_names, filenames in root.walk(follow_symlinks=False):
        base = directory.relative_to(root)
        _prune_walk_directories(directory, directory_names, base, rules, prunes=prunes)
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
