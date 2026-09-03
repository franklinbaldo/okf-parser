"""Optional local DuckDB FTS optimization for RFC 0016 Phase 1B."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol

import duckdb

if TYPE_CHECKING:
    from collections.abc import Sequence


class _PassageLike(Protocol):
    """Minimal passage shape required by the FTS adapter."""

    @property
    def text(self) -> str:
        """Text indexed for lexical retrieval."""
        ...


def _configure_offline_extensions(connection: duckdb.DuckDBPyConnection) -> None:
    """Disable every known-extension path that can install or autoload implicitly."""
    connection.execute("SET autoinstall_known_extensions = false")
    connection.execute("SET autoload_known_extensions = false")


def _load_local_fts(connection: duckdb.DuckDBPyConnection) -> bool:
    """Load FTS only when DuckDB reports an already-installed local extension."""
    installed = connection.execute(
        "SELECT installed, loaded FROM duckdb_extensions() WHERE extension_name = 'fts'"
    ).fetchone()
    if installed is None or not bool(installed[0]):
        return False
    if bool(installed[1]):
        return True
    try:
        connection.execute("LOAD fts")
    except duckdb.Error:
        return False
    return True


def _build_ephemeral_index(
    connection: duckdb.DuckDBPyConnection,
    passages: Sequence[_PassageLike],
) -> None:
    """Materialize and index one invocation's passages in an in-memory database."""
    connection.execute(
        "CREATE TABLE okf_search_passages (passage_id VARCHAR PRIMARY KEY, text VARCHAR)"
    )
    connection.executemany(
        "INSERT INTO okf_search_passages VALUES (?, ?)",
        [(str(index), passage.text) for index, passage in enumerate(passages)],
    )
    connection.execute(
        "PRAGMA create_fts_index("
        "'okf_search_passages', 'passage_id', 'text', "
        "stemmer = 'none', stopwords = 'none', strip_accents = 0, lower = 1"
        ")"
    )


def _query_scores(
    connection: duckdb.DuckDBPyConnection,
    query: str,
) -> list[tuple[int, float]]:
    """Return matched passage indexes and finite BM25 scores."""
    rows = connection.execute(
        """
        SELECT passage_id, score
        FROM (
            SELECT passage_id,
                   fts_main_okf_search_passages.match_bm25(passage_id, ?) AS score
            FROM okf_search_passages
        ) scored
        WHERE score IS NOT NULL
        """,
        [query],
    ).fetchall()
    scores: list[tuple[int, float]] = []
    for passage_id, raw_score in rows:
        score = float(raw_score)
        if math.isfinite(score):
            scores.append((int(passage_id), score))
    return scores


def try_duckdb_fts_scores(
    passages: Sequence[_PassageLike],
    query: str,
) -> list[tuple[int, float]] | None:
    """Use already-installed local DuckDB FTS, or return ``None`` for fallback.

    The connection is in-memory and autoinstall/autoload are disabled before
    extension discovery. This function never issues ``INSTALL`` and treats any
    local FTS load/index/query failure as an instruction to use the deterministic
    built-in lexical engine instead.
    """
    if not passages:
        return None
    try:
        connection = duckdb.connect(":memory:")
    except duckdb.Error:
        return None
    try:
        _configure_offline_extensions(connection)
        if not _load_local_fts(connection):
            return None
        _build_ephemeral_index(connection, passages)
        return _query_scores(connection, query)
    except duckdb.Error:
        return None
    finally:
        connection.close()
