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
from okf_parser.discovery import discover_markdown
from okf_parser.exclusion import ExclusionRules
from okf_parser.models import Severity
from okf_parser.parser import DocumentParseError, is_reserved_document, parse_document

if TYPE_CHECKING:
    from collections.abc import Sequence

_OKF_PREFIX = "__okf_"
_BEFORE_PREFIX = "__okf_before__"
_IDENTITY_COLUMNS = ("__okf_path", "__okf_concept_id", "__okf_logical_key")
_BODY_COLUMNS = ("__okf_body", "__okf_body_lines")
_PROTECTED_COLUMNS = frozenset({*_IDENTITY_COLUMNS, *_BODY_COLUMNS})
_ALTER_ADD_RE = re.compile(r"^\s*ALTER\s+TABLE\s+.+?\s+ADD\s+(?:COLUMN\s+)?", re.IGNORECASE)
_ALTER_DROP_RE = re.compile(r"^\s*ALTER\s+TABLE\s+.+?\s+DROP\s+(?:COLUMN\s+)?", re.IGNORECASE)
_ALTER_RENAME_COLUMN_RE = re.compile(r"^\s*ALTER\s+TABLE\s+.+?\s+RENAME\s+COLUMN\s+", re.IGNORECASE)
_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(.*?)(?:\r?\n)?---[ \t]*(?:\r?\n(.*))?\Z",
    re.DOTALL,
)
_ADD_COLUMN_TYPE_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+.+?\s+ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r'(?:"(?:[^"]|"")+"|\w+)\s+(?P<type>\w+)',
    re.IGNORECASE,
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
                concept_id=path.relative_to(root).with_suffix("").as_posix(),
                concept_type=parsed.concept_type,
                frontmatter=dict(parsed.frontmatter),
                body=parsed.body,
            )
        )
    return concepts


def _scalar_field_names(concepts: Sequence[_Concept]) -> list[str]:
    """Every top-level authored key whose observed value is scalar, sorted."""
    names: set[str] = set()
    for concept in concepts:
        for key, value in concept.frontmatter.items():
            if isinstance(value, str) or value is None:
                names.add(key)
    return sorted(names)


def _check_reserved_and_collisions(type_name: str, field_names: Sequence[str]) -> None:
    for name in field_names:
        if name.startswith(_OKF_PREFIX):
            msg = (
                f'type "{type_name}" has an authored field colliding with the '
                f"reserved __okf_ prefix: {name}"
            )
            raise ApplyError(msg)
    folded: dict[str, str] = {}
    for name in field_names:
        key = name.casefold()
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
) -> dict[str, list[str]]:
    """Build one table (plus its `_before` copy) per type; return its field names."""
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
        # Both identifiers are quoted via _quote_ident, not interpolated raw.
        before_query = (
            f"CREATE TEMP TABLE {_quote_ident(_BEFORE_PREFIX + type_name)} "  # noqa: S608
            f"AS SELECT * FROM {_quote_ident(type_name)}"
        )
        con.execute(before_query)
        fields_by_type[type_name] = field_names
    return fields_by_type


def _parse_script(sql: str) -> tuple[list[str], str]:
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

    *leading, trailing = statements
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
        is_add = _ALTER_ADD_RE.match(query) is not None
        is_drop = _ALTER_DROP_RE.match(query) is not None
        is_rename_column = _ALTER_RENAME_COLUMN_RE.match(query) is not None
        if not (is_add or is_drop or is_rename_column):
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
    con: duckdb.DuckDBPyConnection, fields_by_type: dict[str, list[str]]
) -> str | None:
    changed_types = [
        type_name
        for type_name, field_names in fields_by_type.items()
        if _describe(con, type_name).keys() != {*_PROTECTED_COLUMNS, *field_names}
        or _fetch_rows(con, _BEFORE_PREFIX + type_name) != _fetch_rows(con, type_name)
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
    for column in after_cols - before_cols - _PROTECTED_COLUMNS:
        if after_schema[column].upper() != "VARCHAR":
            msg = f'column "{column}" must be VARCHAR, got {after_schema[column]}'
            raise ApplyError(msg)


def _check_result_rows(
    before_rows: dict[str, dict[str, object]], after_rows: dict[str, dict[str, object]]
) -> None:
    if before_rows.keys() != after_rows.keys():
        msg = "script changed row identity or cardinality"
        raise ApplyError(msg)
    for concept_id, before_row in before_rows.items():
        after_row = after_rows[concept_id]
        for column in _PROTECTED_COLUMNS:
            if before_row.get(column) != after_row.get(column):
                msg = f'row "{concept_id}" changed protected column "{column}"'
                raise ApplyError(msg)


def _execute_script(
    con: duckdb.DuckDBPyConnection,
    fields_by_type: dict[str, list[str]],
    alter_queries: list[str],
    update_query: str,
) -> _ScriptOutcome:
    """Run the script transactionally and validate its result.

    Raises :class:`ApplyError` for any script failure or contract violation.
    Nothing about the real bundle is at risk at any point here: only the
    ephemeral in-memory database is touched.
    """
    _run_transaction(con, alter_queries, update_query)

    touched = _find_touched_type(con, fields_by_type)
    if touched is None:
        return _ScriptOutcome(touched_type=None)

    before_cols = {*_PROTECTED_COLUMNS, *fields_by_type[touched]}
    after_schema = _describe(con, touched)
    _check_result_schema(after_schema, before_cols)

    before_rows = _fetch_rows(con, _BEFORE_PREFIX + touched)
    after_rows = _fetch_rows(con, touched)
    _check_result_rows(before_rows, after_rows)

    # The union of before- and after-authored fields, not just after: a column
    # dropped or renamed away must still be visited so its old key is deleted
    # from frontmatter, even though it is no longer part of the after schema.
    field_names = sorted((set(after_schema) | set(fields_by_type[touched])) - _PROTECTED_COLUMNS)
    row_diffs = tuple(
        _RowDiff(
            concept_id=concept_id,
            changed_fields={
                name: _as_text(after_row.get(name))
                for name in field_names
                if before_row.get(name) != after_row.get(name)
            },
        )
        for concept_id, before_row in before_rows.items()
        for after_row in (after_rows[concept_id],)
        if any(before_row.get(name) != after_row.get(name) for name in field_names)
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

        baseline = validate_path(root, exclude)
        baseline_keys = {
            (v.code, v.path, v.message) for v in baseline.violations if v.severity == Severity.ERROR
        }

        concepts = _load_concepts(root, exclude)
        con = duckdb.connect()
        fields_by_type = _materialize(con, concepts)
        outcome = _execute_script(con, fields_by_type, alter_queries, update_query)
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

    return _stage_validate_write(root, exclude, candidates, baseline_keys, changed_paths)


def _stage_validate_write(
    root: Path,
    exclude: Sequence[str],
    candidates: dict[str, tuple[_RawDocument, str, Path]],
    baseline_keys: set[tuple[str, str, str]],
    changed_paths: list[str],
) -> dict[str, object]:
    touched_hashes = {rel: _sha256(path) for rel, (_, _, path) in candidates.items()}

    with tempfile.TemporaryDirectory(prefix="okf-apply-") as tmp:
        candidate_root = Path(tmp) / "bundle"
        candidate_root.mkdir()
        _build_candidate_tree(root, candidate_root, candidates)

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

        conflicts = [
            rel for rel, (_, _, real) in candidates.items() if _sha256(real) != touched_hashes[rel]
        ]
        if conflicts:
            return ApplyResult(
                succeeded=False,
                conflict_paths=tuple(sorted(conflicts)),
                error="one or more touched documents changed since apply read them",
            ).to_dict()

        for raw, frontmatter_text, real_path in candidates.values():
            _write_raw(real_path, raw, frontmatter_text)

    return ApplyResult(changed_paths=tuple(sorted(changed_paths)), written=True).to_dict()


def _build_candidate_tree(
    root: Path, candidate_root: Path, candidates: dict[str, tuple[_RawDocument, str, Path]]
) -> None:
    for source in root.rglob("*"):
        if source.is_dir():
            continue
        relative = source.relative_to(root)
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
