"""Import any DuckDB-readable source (CSV, Parquet, (ND)JSON) into an OKF bundle.

The binary does it (``__import``, ``okf-db/src/import.rs``): every row becomes
``<type slug>/<id slug>.md`` with ``type`` and every non-null column as text.
A dry run returns a ``preview_token`` bound to the source rows and to every
destination; a write that passes it back fails closed if either changed.
Existing destinations are skipped, verified (``verify-identical``: the same
parsed document matches, any other conflicts and blocks the import) or, with
``overwrite``, replaced.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from okf_parser.rust_core import native_result


class BundleImportError(ValueError):
    """Raised when a source cannot be read or conflicts with import identity."""


type ImportConflictPolicy = Literal["skip", "verify-identical"]


class _ImportRequest(BaseModel):
    """The ``__import`` request the binary validates."""

    model_config = ConfigDict(frozen=True)

    source: str
    path: str
    concept_type: str
    id_column: str | None
    write: bool
    overwrite: bool
    on_conflict: ImportConflictPolicy
    expected_preview_token: str | None


def import_bundle(
    source: str,
    path: str,
    concept_type: str,
    *,
    id_column: str | None = None,
    write: bool = False,
    overwrite: bool = False,
    on_conflict: ImportConflictPolicy = "skip",
    expected_preview_token: str | None = None,
) -> dict[str, JsonValue]:
    """Materialize every row of a DuckDB-readable source as one concept document.

    Dry-run by default: ``write=False`` only reports what would be created.
    """
    return native_result(
        "__import",
        _ImportRequest(
            source=source,
            path=path,
            concept_type=concept_type,
            id_column=id_column,
            write=write,
            overwrite=overwrite,
            on_conflict=on_conflict,
            expected_preview_token=expected_preview_token,
        ),
        {"request": BundleImportError},
    )
