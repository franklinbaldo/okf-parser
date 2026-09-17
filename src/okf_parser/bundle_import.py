"""Import any DuckDB-readable source (CSV, Parquet, (ND)JSON) into an OKF bundle.

Not RFC 0007's writeback: RFC 0007 is specifically the DuckDB-catalog-back-
to-frontmatter problem RFC 0006 deferred to it, over a bundle that already
exists. This is the opposite direction and a different system: an external
tabular source becomes a *new* bundle of concept documents, symmetric with
`okf_parser.duckdb`, which already goes from an existing bundle to DuckDB
tables. DuckDB's own replacement scan (`FROM '<source>'`) resolves CSV,
Parquet, and (ND)JSON by extension with no format-specific flag; a source it
cannot open on its own surfaces as DuckDB's own error, unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import duckdb
from ruamel.yaml import YAML

from okf_parser.parser import DocumentParseError, parse_document, parse_document_text
from okf_parser.rust_core import resolve_rust_core, rust_render_frontmatter
from okf_parser.type_specs import type_slug

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class BundleImportError(ValueError):
    """Raised when a source cannot be read or conflicts with import identity."""


type ImportConflictPolicy = Literal["skip", "verify-identical"]


def _read_rows(source: str) -> tuple[list[str], list[dict[str, object]]]:
    escaped = source.replace("'", "''")
    con = duckdb.connect()
    try:
        try:
            # The string literal is '-doubled above, not interpolated raw.
            relation = con.sql(f"SELECT * FROM '{escaped}'")
        except duckdb.Error as exc:
            message = f"could not read {source!r}: {exc}"
            raise BundleImportError(message) from exc
        columns = list(relation.columns)
        rows = [dict(zip(columns, row, strict=True)) for row in relation.fetchall()]
    finally:
        con.close()
    return columns, rows


def _concept_id(row: dict[str, object], index: int, id_column: str | None) -> str:
    if id_column is None:
        return f"{index:06d}"
    value = row.get(id_column)
    if value is None or (isinstance(value, str) and not value.strip()):
        message = f"row {index} has no value in id column {id_column!r}"
        raise BundleImportError(message)
    return str(value)


def _json_safe(value: object) -> object:
    """Preserve JSON-shaped DuckDB values; stringify only unsupported scalars."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _frontmatter_mapping(concept_type: str, row: dict[str, object]) -> dict[str, object]:
    data: dict[str, object] = {"type": concept_type}
    for key, value in row.items():
        if key != "type" and value is not None:
            data[key] = _json_safe(value)
    return data


def _fallback_frontmatter_bytes(data: dict[str, object]) -> bytes:
    """Source-checkout fallback mirroring the Rust writer's typed semantics."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    buffer = StringIO()
    yaml.dump(data, buffer)
    text = buffer.getvalue().replace("\r\n", "\n").replace("\r", "\n")
    return f"---\n{text.rstrip(chr(10))}\n---\n".encode()


def _frontmatter_bytes(concept_type: str, row: dict[str, object]) -> bytes:
    data = _frontmatter_mapping(concept_type, row)
    core = resolve_rust_core()
    if core is not None:
        return rust_render_frontmatter(data, core)
    return _fallback_frontmatter_bytes(data)


def _plan(
    concept_type: str,
    columns: Sequence[str],
    rows: Sequence[dict[str, object]],
    id_column: str | None,
) -> tuple[dict[str, tuple[str, dict[str, object]]], tuple[str, ...]]:
    """Split rows into ``concept_id -> (relative path, row)`` and duplicate id slugs.

    A duplicate - two rows deriving the same filesystem slug from the id
    column - blocks the whole call, the same fail-closed posture `init`
    already takes toward a derived-path collision: partial imports that
    silently drop rows are worse than none.
    """
    if id_column is not None and id_column not in columns:
        message = f"id column {id_column!r} is not a column of the source: {list(columns)}"
        raise BundleImportError(message)
    type_dir = type_slug(concept_type) or "concept"
    seen: dict[str, int] = {}
    plan: dict[str, tuple[str, dict[str, object]]] = {}
    for index, row in enumerate(rows):
        concept_id = _concept_id(row, index, id_column)
        id_slug = type_slug(concept_id) or f"row-{index:06d}"
        seen[id_slug] = seen.get(id_slug, 0) + 1
        plan[f"{type_dir}/{id_slug}.md"] = (id_slug, row)
    duplicates = tuple(sorted(slug for slug, count in seen.items() if count > 1))
    return plan, duplicates


def _matches_candidate(destination: Path, candidate: bytes) -> bool:
    """Compare the parser value, not incidental YAML spelling."""
    try:
        existing = parse_document(destination)
        intended = parse_document_text(destination, candidate.decode("utf-8"))
    except (DocumentParseError, UnicodeDecodeError):
        return False
    return existing.parsed_digest == intended.parsed_digest


def _destination_state(destination: Path) -> dict[str, str]:
    """Return the destination state that must remain unchanged until commit."""
    if destination.is_symlink():
        return {"kind": "symlink", "target": str(destination.readlink())}
    if destination.is_file():
        return {
            "kind": "file",
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        }
    if destination.exists():
        return {"kind": "other"}
    return {"kind": "absent"}


def _stable_rows(columns: Sequence[str], rows: Sequence[dict[str, object]]) -> list[list[object]]:
    """Preserve the effective source values in a JSON-stable representation."""
    stable: list[list[object]] = []
    for row in rows:
        values: list[object] = []
        for column in columns:
            value = row.get(column)
            if value is None:
                values.append(None)
            elif isinstance(value, str):
                values.append(["str", value])
            else:
                values.append([type(value).__qualname__, str(value)])
        stable.append(values)
    return stable


def _preview_token(
    *,
    source: str,
    concept_type: str,
    id_column: str | None,
    overwrite: bool,
    on_conflict: ImportConflictPolicy,
    columns: Sequence[str],
    rows: Sequence[dict[str, object]],
    destinations: dict[str, dict[str, str]],
) -> str:
    """Bind an import preview to its source plan and current destination state."""
    payload = {
        "version": 1,
        "source": source,
        "concept_type": concept_type,
        "id_column": id_column,
        "overwrite": overwrite,
        "on_conflict": on_conflict,
        "columns": list(columns),
        "rows": _stable_rows(columns, rows),
        "destinations": [[relative, destinations[relative]] for relative in sorted(destinations)],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_options(*, overwrite: bool, on_conflict: ImportConflictPolicy) -> None:
    if overwrite and on_conflict == "verify-identical":
        message = "overwrite and on_conflict='verify-identical' are mutually exclusive"
        raise BundleImportError(message)


def _validate_preview_token(
    *, write: bool, preview_token: str, expected_preview_token: str | None
) -> None:
    """Fail closed when a reviewed import preview no longer matches current state."""
    if (
        write
        and expected_preview_token is not None
        and not hmac.compare_digest(preview_token, expected_preview_token)
    ):
        message = "import preview is stale; rerun preview before committing"
        raise BundleImportError(message)


def import_bundle(  # each argument is an independent public CLI flag.
    source: str,
    path: str,
    concept_type: str,
    *,
    id_column: str | None = None,
    write: bool = False,
    overwrite: bool = False,
    on_conflict: ImportConflictPolicy = "skip",
    expected_preview_token: str | None = None,
) -> dict[str, object]:
    """Materialize every row of a DuckDB-readable source as one concept document.

    Dry-run by default (`write=False` only reports what would be created).
    Existing destinations use the requested conflict policy: `skip` preserves
    the historical behavior, while `verify-identical` treats the same parsed
    concept value as an idempotent match and any divergence as an atomic
    conflict. `overwrite` remains an explicit replacement policy and cannot be
    combined with `verify-identical`.

    Dry-runs return a deterministic ``preview_token``. When a write supplies
    ``expected_preview_token``, source values and every relevant destination
    must still match that reviewed preview or the call fails before any
    filesystem mutation.
    """
    _validate_options(overwrite=overwrite, on_conflict=on_conflict)
    root = Path(path).resolve()
    columns, rows = _read_rows(source)
    if "type" in columns:
        message = "source column 'type' is reserved; use --type to set concept identity"
        raise BundleImportError(message)
    plan, duplicate_ids = _plan(concept_type, columns, rows, id_column)
    destinations = {relative: _destination_state(root / relative) for relative in plan}
    preview_token = _preview_token(
        source=source,
        concept_type=concept_type,
        id_column=id_column,
        overwrite=overwrite,
        on_conflict=on_conflict,
        columns=columns,
        rows=rows,
        destinations=destinations,
    )
    _validate_preview_token(
        write=write,
        preview_token=preview_token,
        expected_preview_token=expected_preview_token,
    )
    if duplicate_ids:
        return {
            "created": [],
            "would_create": [],
            "skipped_existing": [],
            "matched_existing": [],
            "conflicting_existing": [],
            "duplicate_ids": list(duplicate_ids),
            "preview_token": preview_token,
            "written": False,
        }

    to_write: dict[str, bytes] = {}
    skipped_existing: list[str] = []
    matched_existing: list[str] = []
    conflicting_existing: list[str] = []
    for relative, (_, row) in plan.items():
        destination = root / relative
        candidate = _frontmatter_bytes(concept_type, row)
        if destination.is_file() and not overwrite:
            if on_conflict == "skip":
                skipped_existing.append(relative)
                continue
            if _matches_candidate(destination, candidate):
                matched_existing.append(relative)
            else:
                conflicting_existing.append(relative)
            continue
        to_write[relative] = candidate

    if conflicting_existing:
        return {
            "created": [],
            "would_create": sorted(to_write),
            "skipped_existing": sorted(skipped_existing),
            "matched_existing": sorted(matched_existing),
            "conflicting_existing": sorted(conflicting_existing),
            "duplicate_ids": [],
            "preview_token": preview_token,
            "written": False,
        }

    if not write:
        return {
            "created": [],
            "would_create": sorted(to_write),
            "skipped_existing": sorted(skipped_existing),
            "matched_existing": sorted(matched_existing),
            "conflicting_existing": [],
            "duplicate_ids": [],
            "preview_token": preview_token,
            "written": False,
        }

    created: list[str] = []
    for relative, content in to_write.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Stage and rename like write_support.write_raw does for apply/edit.
        staged = destination.with_name(f".{destination.name}.okf-write.tmp")
        staged.write_bytes(content)
        staged.replace(destination)
        created.append(relative)
    return {
        "created": sorted(created),
        "would_create": [],
        "skipped_existing": sorted(skipped_existing),
        "matched_existing": sorted(matched_existing),
        "conflicting_existing": [],
        "duplicate_ids": [],
        "preview_token": preview_token,
        "written": True,
    }
