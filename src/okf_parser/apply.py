"""Frontmatter edits written as SQL (RFC 0005), run by the native binary.

Each concept type is a table named for its ``type``. Scalar fields are
``VARCHAR`` columns and flat scalar lists are list columns. The script runs in
a sandboxed in-memory DuckDB, and the final tables are the answer: a changed
value sets the field, ``NULL`` removes it, and a dropped (or renamed) column
removes it where it was authored. Rows and the ``__okf_*`` columns cannot
change; mappings, nested lists, and scalar/list mixtures are not writable.
Declared scalar fields stay typed/read-only, while declared list fields are
typed and writable. See ``okf-db/src/apply.rs``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from okf_parser.rust_core import native_result

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue


class ApplyError(ValueError):
    """Raised when an apply request is incomplete (neither SQL nor the full shorthand)."""


class _ApplyRequest(BaseModel):
    """The ``__apply`` request the binary validates."""

    model_config = ConfigDict(frozen=True, serialize_by_alias=True)

    path: str
    sql: str | None
    type_name: str | None = Field(serialization_alias="type")
    field: str | None
    from_value: str | None = Field(serialization_alias="from")
    to: str | None
    exclude: list[str]
    spec_template: str | None
    write: bool
    expected_preview_token: str | None


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
    expected_preview_token: str | None = None,
) -> dict[str, JsonValue]:
    """Preview, or with ``write`` commit, a frontmatter edit written as SQL.

    ``sql`` is the script; ``type_name``/``field_name``/``from_value``/
    ``to_value`` together are the shorthand for one ``UPDATE``. A script that
    fails, or breaks the rules, answers with ``succeeded: false`` and
    ``error``; an incomplete request raises :class:`ApplyError`.
    """
    return native_result(
        "__apply",
        _ApplyRequest(
            path=str(Path(path).resolve()),
            sql=sql,
            type_name=type_name,
            field=field_name,
            from_value=from_value,
            to=to_value,
            exclude=list(exclude),
            spec_template=spec_template,
            write=write,
            expected_preview_token=expected_preview_token,
        ),
        {"request": ApplyError},
    )