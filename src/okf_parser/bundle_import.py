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

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
from ruamel.yaml import YAML

from okf_parser.type_specs import type_slug

if TYPE_CHECKING:
    from collections.abc import Sequence


class BundleImportError(ValueError):
    """Raised when a source cannot be read or conflicts with import identity."""


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


def _frontmatter_text(yaml: YAML, concept_type: str, row: dict[str, object]) -> str:
    data: dict[str, object] = {"type": concept_type}
    for key, value in row.items():
        if key == "type":
            continue
        if value is not None:
            data[key] = value if isinstance(value, str) else str(value)
    buffer = StringIO()
    yaml.dump(data, buffer)
    return buffer.getvalue()


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


def import_bundle(  # each argument is an independent public CLI flag.
    source: str,
    path: str,
    concept_type: str,
    *,
    id_column: str | None = None,
    write: bool = False,
    overwrite: bool = False,
) -> dict[str, object]:
    """Materialize every row of a DuckDB-readable source as one concept document.

    Dry-run by default (`write=False` only reports what would be created).
    `overwrite=False` (the default) never touches a file that already
    exists at its destination - a matched destination is reported as
    skipped, not silently replaced. A non-unique id-column slug blocks the
    whole call before anything is written. Source data may not define the
    reserved `type` key because `--type` is the import's canonical concept
    identity.
    """
    root = Path(path).resolve()
    columns, rows = _read_rows(source)
    if "type" in columns:
        message = "source column 'type' is reserved; use --type to set concept identity"
        raise BundleImportError(message)
    plan, duplicate_ids = _plan(concept_type, columns, rows, id_column)
    if duplicate_ids:
        return {
            "created": [],
            "would_create": [],
            "skipped_existing": [],
            "duplicate_ids": list(duplicate_ids),
            "written": False,
        }

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096

    to_write: dict[str, str] = {}
    skipped_existing: list[str] = []
    for relative, (_, row) in plan.items():
        if (root / relative).is_file() and not overwrite:
            skipped_existing.append(relative)
            continue
        to_write[relative] = "---\n" + _frontmatter_text(yaml, concept_type, row) + "---\n"

    if not write:
        return {
            "created": [],
            "would_create": sorted(to_write),
            "skipped_existing": sorted(skipped_existing),
            "duplicate_ids": [],
            "written": False,
        }

    created: list[str] = []
    for relative, text in to_write.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        created.append(relative)
    return {
        "created": sorted(created),
        "would_create": [],
        "skipped_existing": sorted(skipped_existing),
        "duplicate_ids": [],
        "written": True,
    }
