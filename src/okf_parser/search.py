"""Offline bundle search (RFC 0016 phase 1): ``Bundle.search()``.

The binary answers it (``__search``) over the snapshot the ``Bundle`` holds:
every non-blank body line is a passage, ``lexical`` ranks passages with a
deterministic BM25 scorer over case-folded words and ``literal`` keeps those
containing the case-folded query. ``compact`` and ``score`` answer TSV rows,
``full`` a mapping with each hit's provenance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from okf_parser.rust_core import native_result
from okf_parser.sql import bundle_records

if TYPE_CHECKING:
    from okf_parser.bundle import Bundle

type SearchMode = Literal["lexical", "literal"]
type SearchDetail = Literal["compact", "score", "full"]


class SearchError(ValueError):
    """Raised when a search request violates the RFC 0016 contract."""


class _SearchRequest(BaseModel):
    """The ``__search`` request the binary validates."""

    model_config = ConfigDict(frozen=True)

    bundle: dict[str, JsonValue]
    query: str
    mode: str
    limit: int
    context: int
    concept_type: str | None
    path_glob: str | None
    detail: str
    profile: str | None


def search_bundle(  # noqa: PLR0913 - the RFC 0016 request is intentionally flat.
    bundle: Bundle,
    query: str,
    *,
    mode: SearchMode | str = "lexical",
    limit: int = 10,
    context: int = 0,
    concept_type: str | None = None,
    path_glob: str | None = None,
    detail: SearchDetail | str = "compact",
    profile: str | None = None,
) -> str | dict[str, object]:
    """Search one already-loaded bundle without filesystem or network access."""
    result = native_result(
        "__search",
        _SearchRequest(
            bundle=dict(bundle_records(bundle)),
            query=query,
            mode=mode,
            limit=limit,
            context=context,
            concept_type=concept_type,
            path_glob=path_glob,
            detail=detail,
            profile=profile,
        ),
        {"request": SearchError},
    )
    rows = result.get("rows")
    return rows if isinstance(rows, str) else dict(result)
