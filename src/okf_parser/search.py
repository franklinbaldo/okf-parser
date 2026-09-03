"""Offline bundle search primitives for RFC 0016 Phase 1."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from okf_parser.body_lines import body_lines
from okf_parser.exclusion import ExclusionRules
from okf_parser.search_fts import try_duckdb_fts_scores

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from okf_parser.bundle import Bundle

_BUILTIN_LEXICAL_ENGINE = "builtin_lexical_v1"
_DUCKDB_FTS_ENGINE = "duckdb_fts"
_LITERAL_ENGINE = "literal_v1"
_BM25_K1 = 1.2
_BM25_B = 0.75
_ALLOWED_MODES = frozenset({"lexical", "literal", "vector", "hybrid"})
_ALLOWED_DETAILS = frozenset({"compact", "score", "full"})


class SearchError(ValueError):
    """Raised when a search request violates the RFC 0016 contract."""


@dataclass(frozen=True, slots=True)
class _Passage:
    """One rankable body-line passage with exact body coordinates."""

    concept_id: str
    concept_type: str
    path: str
    source_digest: str
    body: tuple[str, ...]
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class _Hit:
    """One scored passage before optional context expansion."""

    passage: _Passage
    score: float


def _required_text(row: Mapping[str, object], key: str) -> str:
    """Read one required textual concept column from an executed bundle row."""
    value = row.get(key)
    if not isinstance(value, str):
        msg = f"bundle concept row has non-text {key}: {value!r}"
        raise SearchError(msg)
    return value


def _validate_path_glob(path_glob: str) -> ExclusionRules:
    """Compile one positive RFC 0004 path pattern using the existing matcher."""
    rules = ExclusionRules(patterns=(path_glob,))
    if not rules.rules:
        msg = "path_glob must be one non-empty positive path pattern"
        raise SearchError(msg)
    if path_glob.startswith("!"):
        msg = "path_glob does not support negation"
        raise SearchError(msg)
    return rules


def _passages(
    bundle: Bundle,
    *,
    concept_type: str | None,
    path_glob: str | None,
) -> list[_Passage]:
    """Build Phase 1 line passages from already-discovered bundle concepts."""
    matcher = _validate_path_glob(path_glob) if path_glob is not None else None
    frame = bundle.concepts.execute()
    rows = cast("list[dict[str, object]]", frame.to_dict(orient="records"))
    passages: list[_Passage] = []
    for row in rows:
        row_type = _required_text(row, "concept_type")
        if concept_type is not None and row_type != concept_type:
            continue
        path = _required_text(row, "path")
        if matcher is not None and not matcher.excludes(path):
            continue
        lines = tuple(body_lines(_required_text(row, "body")))
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            passages.append(
                _Passage(
                    concept_id=_required_text(row, "concept_id"),
                    concept_type=row_type,
                    path=path,
                    source_digest=_required_text(row, "source_digest"),
                    body=lines,
                    start=index,
                    end=index,
                    text=line,
                )
            )
    return passages


def _lexical_tokens(text: str) -> tuple[str, ...]:
    """Tokenize text for the versioned built-in lexical engine.

    Tokenization is intentionally small and portable: Unicode default case
    folding followed by maximal runs of alphanumeric characters or underscore.
    """
    tokens: list[str] = []
    current: list[str] = []
    for character in text.casefold():
        if character.isalnum() or character == "_":
            current.append(character)
            continue
        if current:
            tokens.append("".join(current))
            current.clear()
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _lexical_hits(passages: Sequence[_Passage], query: str) -> list[_Hit]:
    """Rank passages with the deterministic built-in BM25 scorer."""
    query_terms = tuple(dict.fromkeys(_lexical_tokens(query)))
    if not query_terms or not passages:
        return []

    tokenized = [_lexical_tokens(passage.text) for passage in passages]
    document_count = len(tokenized)
    average_length = sum(len(tokens) for tokens in tokenized) / document_count
    if average_length == 0:
        return []

    document_frequency = {
        term: sum(term in tokens for tokens in tokenized)
        for term in query_terms
    }
    inverse_document_frequency = {
        term: math.log(
            1.0
            + (document_count - frequency + 0.5)
            / (frequency + 0.5)
        )
        for term, frequency in document_frequency.items()
        if frequency
    }

    hits: list[_Hit] = []
    for passage, tokens in zip(passages, tokenized, strict=True):
        frequencies = Counter(tokens)
        length_normalizer = _BM25_K1 * (
            1.0 - _BM25_B + _BM25_B * len(tokens) / average_length
        )
        score = 0.0
        for term, idf in inverse_document_frequency.items():
            frequency = frequencies[term]
            if frequency:
                score += idf * (
                    frequency * (_BM25_K1 + 1.0)
                    / (frequency + length_normalizer)
                )
        if score > 0.0:
            hits.append(_Hit(passage=passage, score=score))
    return hits


def _local_fts_hits(passages: Sequence[_Passage], query: str) -> list[_Hit] | None:
    """Resolve optional local FTS scores back to the already-built passages."""
    scores = try_duckdb_fts_scores(passages, query)
    if scores is None:
        return None
    return [
        _Hit(passage=passages[index], score=score)
        for index, score in scores
        if 0 <= index < len(passages)
    ]


def _literal_hits(passages: Sequence[_Passage], query: str) -> list[_Hit]:
    """Return Unicode-case-folded literal substring matches."""
    needle = query.casefold()
    return [
        _Hit(passage=passage, score=1.0)
        for passage in passages
        if needle in passage.text.casefold()
    ]


def _ordered_unique(hits: Sequence[_Hit]) -> list[_Hit]:
    """Coalesce duplicate raw ranges and apply the canonical structural tie-break."""
    best: dict[tuple[str, int, int], _Hit] = {}
    for hit in hits:
        key = (hit.passage.path, hit.passage.start, hit.passage.end)
        previous = best.get(key)
        if previous is None or hit.score > previous.score:
            best[key] = hit
    return sorted(
        best.values(),
        key=lambda hit: (
            -hit.score,
            hit.passage.path,
            hit.passage.start,
            hit.passage.end,
        ),
    )


def _escape_location_path(path: str) -> str:
    """Escape the compact location path without changing raw path identity."""
    escaped: list[str] = []
    for character in path:
        codepoint = ord(character)
        if character == "%":
            escaped.append("%25")
        elif character == "#":
            escaped.append("%23")
        elif codepoint <= 0x1F or codepoint == 0x7F:
            escaped.append(f"%{codepoint:02X}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _location(path: str, start: int, end: int) -> str:
    """Render one canonical ephemeral body-relative location."""
    escaped = _escape_location_path(path)
    if start == end:
        return f"{escaped}#B{start}"
    return f"{escaped}#B{start}-B{end}"


def _expanded_range(passage: _Passage, context: int) -> tuple[int, int, tuple[str, ...]]:
    """Expand one selected hit after ranking, bounded by its concept body."""
    start = max(1, passage.start - context)
    end = min(len(passage.body), passage.end + context)
    return start, end, passage.body[start - 1 : end]


def _compact_snippet(lines: Sequence[str]) -> str:
    """Render complete selected body lines under the RFC 0016 whitelist."""
    return " ".join(line.replace("\t", " ") for line in lines)


def _score_text(score: float) -> str:
    """Render one finite higher-is-better score deterministically."""
    if not math.isfinite(score):
        msg = "search engine produced a non-finite score"
        raise SearchError(msg)
    return format(score, ".12g")


def _render_compact(hits: Sequence[_Hit], *, context: int, with_score: bool) -> str:
    """Render compact or score-detail TSV output."""
    header = "location\tscore\tsnippet" if with_score else "location\tsnippet"
    rows = [header]
    for hit in hits:
        start, end, lines = _expanded_range(hit.passage, context)
        location = _location(hit.passage.path, start, end)
        snippet = _compact_snippet(lines)
        if with_score:
            rows.append(f"{location}\t{_score_text(hit.score)}\t{snippet}")
        else:
            rows.append(f"{location}\t{snippet}")
    return "\n".join(rows)


def _render_full(
    hits: Sequence[_Hit],
    *,
    query: str,
    mode: str,
    profile: str | None,
    context: int,
    engine: str,
) -> dict[str, object]:
    """Render the stable-core structured result plus non-semantic diagnostics."""
    results: list[dict[str, object]] = []
    for rank, hit in enumerate(hits, start=1):
        start, end, lines = _expanded_range(hit.passage, context)
        results.append(
            {
                "rank": rank,
                "score": hit.score,
                "concept_id": hit.passage.concept_id,
                "concept_type": hit.passage.concept_type,
                "path": hit.passage.path,
                "location": _location(hit.passage.path, start, end),
                "body_start_line": start,
                "body_end_line": end,
                "source_digest": hit.passage.source_digest,
                "text": "\n".join(lines),
            }
        )
    return {
        "query": query,
        "mode": mode,
        "profile": profile,
        "results": results,
        "diagnostics": {"engine": engine},
    }


def search_bundle(  # noqa: PLR0913 - public RFC 0016 schema is intentionally flat.
    bundle: Bundle,
    query: str,
    *,
    mode: str = "lexical",
    limit: int = 10,
    context: int = 0,
    concept_type: str | None = None,
    path_glob: str | None = None,
    detail: str = "compact",
    profile: str | None = None,
) -> str | dict[str, object]:
    """Search one already-loaded OKF bundle without filesystem or network access."""
    normalized_query = query.strip()
    if not normalized_query:
        msg = "query must not be empty"
        raise SearchError(msg)
    if limit < 1:
        msg = "limit must be at least 1"
        raise SearchError(msg)
    if context < 0:
        msg = "context must be non-negative"
        raise SearchError(msg)
    if mode not in _ALLOWED_MODES:
        msg = f"unsupported search mode: {mode}"
        raise SearchError(msg)
    if detail not in _ALLOWED_DETAILS:
        msg = f"unsupported search detail: {detail}"
        raise SearchError(msg)
    if profile is not None:
        msg = f"profile is not configured: {profile}"
        raise SearchError(msg)
    if mode in {"vector", "hybrid"}:
        msg = f"{mode} retrieval is not configured"
        raise SearchError(msg)

    candidates = _passages(
        bundle,
        concept_type=concept_type,
        path_glob=path_glob,
    )
    if mode == "literal":
        hits = _literal_hits(candidates, normalized_query)
        engine = _LITERAL_ENGINE
    else:
        fts_hits = _local_fts_hits(candidates, normalized_query)
        if fts_hits is None:
            hits = _lexical_hits(candidates, normalized_query)
            engine = _BUILTIN_LEXICAL_ENGINE
        else:
            hits = fts_hits
            engine = _DUCKDB_FTS_ENGINE

    selected = _ordered_unique(hits)[:limit]
    if detail == "compact":
        return _render_compact(selected, context=context, with_score=False)
    if detail == "score":
        return _render_compact(selected, context=context, with_score=True)
    return _render_full(
        selected,
        query=normalized_query,
        mode=mode,
        profile=profile,
        context=context,
        engine=engine,
    )
