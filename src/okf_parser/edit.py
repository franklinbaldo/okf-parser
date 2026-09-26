"""Conflict-safe single-concept Markdown-body editing.

Frontmatter mutation stays with :mod:`okf_parser.apply`. This fills the
orthogonal gap a document editor needs: replacing one concept's Markdown body
without exposing arbitrary filesystem writes. The native binary does the work
(``okf-engine/src/write.rs``): it snapshots the bundle, stages and validates a
candidate, rechecks freshness and only then replaces the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from okf_parser.rust_core import RustCoreError, call_native

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue


class EditError(ValueError):
    """Raised when a single-concept edit request is structurally invalid."""


class _EditRequest(BaseModel):
    """The ``__edit`` request the binary validates."""

    model_config = ConfigDict(frozen=True)

    path: str
    concept_id: str
    body: str
    expected_source_digest: str
    exclude: list[str]
    write: bool


def _edit_concept(  # noqa: PLR0913 -- independent edit safety inputs.
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    *,
    write: bool,
    exclude: Sequence[str],
) -> dict[str, JsonValue]:
    try:
        body.encode("utf-8")
    except UnicodeEncodeError as exc:
        msg = "body must be valid UTF-8 text"
        raise EditError(msg) from exc
    response = call_native(
        "__edit",
        _EditRequest(
            path=str(Path(path)),
            concept_id=concept_id,
            body=body,
            expected_source_digest=expected_source_digest,
            exclude=list(exclude),
            write=write,
        ),
    )
    if response.error is not None:
        if response.error.kind == "request":
            raise EditError(response.error.message)
        raise RustCoreError(response.error.message)
    if response.result is None:
        msg = "okf-parser __edit answered with neither a result nor an error"
        raise RustCoreError(msg)
    return response.result


def preview_concept_edit(
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    *,
    exclude: Sequence[str] = (),
) -> dict[str, JsonValue]:
    """Preview one body replacement without touching the live bundle."""
    return _edit_concept(
        path,
        concept_id,
        body,
        expected_source_digest,
        write=False,
        exclude=exclude,
    )


def write_concept_edit(
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    *,
    exclude: Sequence[str] = (),
) -> dict[str, JsonValue]:
    """Commit one body replacement after staging, validation and freshness checks."""
    return _edit_concept(
        path,
        concept_id,
        body,
        expected_source_digest,
        write=True,
        exclude=exclude,
    )
