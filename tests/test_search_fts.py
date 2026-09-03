"""Tests for the optional local DuckDB FTS optimization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, cast

import okf_parser.search as search_module
import okf_parser.search_fts as fts_module
from okf_parser.bundle import load_bundle
from okf_parser.search import search_bundle

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


@dataclass(frozen=True, slots=True)
class _PassageStub:
    """Minimal immutable passage used by direct FTS adapter tests."""

    text: str


class _FakeDuckDBConnection:
    """Small recorder for the DuckDB calls relevant to extension safety."""

    def __init__(
        self,
        *,
        installed: bool,
        loaded: bool = False,
        scores: list[tuple[str, float]] | None = None,
    ) -> None:
        self.installed = installed
        self.loaded = loaded
        self.scores = scores or []
        self.statements: list[str] = []
        self.closed = False

    def execute(self, sql: str, parameters: object | None = None) -> Self:
        del parameters
        self.statements.append(sql.strip())
        return self

    def executemany(self, sql: str, parameters: object) -> Self:
        del parameters
        self.statements.append(sql.strip())
        return self

    def fetchone(self) -> tuple[bool, bool]:
        return self.installed, self.loaded

    def fetchall(self) -> list[tuple[str, float]]:
        return self.scores

    def close(self) -> None:
        self.closed = True


def _write_concept(path: Path, body: str) -> None:
    path.write_text(f"---\ntype: Tese\n---\n{body}", encoding="utf-8")


def _assert_no_install(statements: list[str]) -> None:
    assert all(not statement.upper().startswith("INSTALL ") for statement in statements)


def test_fts_unavailable_disables_autoload_and_falls_back_without_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeDuckDBConnection(installed=False)
    monkeypatch.setattr(fts_module.duckdb, "connect", lambda _database: connection)

    scores = fts_module.try_duckdb_fts_scores([_PassageStub("ignored")], "needle")

    assert scores is None
    assert connection.statements[:3] == [
        "SET autoinstall_known_extensions = false",
        "SET autoload_known_extensions = false",
        "SELECT installed, loaded FROM duckdb_extensions() WHERE extension_name = 'fts'",
    ]
    _assert_no_install(connection.statements)
    assert connection.closed


def test_already_installed_fts_is_loaded_and_queried_without_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeDuckDBConnection(
        installed=True,
        loaded=False,
        scores=[("1", 7.5), ("0", 2.0)],
    )
    monkeypatch.setattr(fts_module.duckdb, "connect", lambda _database: connection)
    passages = [_PassageStub("one"), _PassageStub("two")]

    scores = fts_module.try_duckdb_fts_scores(passages, "needle")

    assert scores == [(1, 7.5), (0, 2.0)]
    assert "LOAD fts" in connection.statements
    assert any("create_fts_index" in statement for statement in connection.statements)
    _assert_no_install(connection.statements)
    assert connection.closed


def test_search_prefers_local_fts_scores_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_concept(tmp_path / "a.md", "first needle\nsecond needle")

    def fake_scores(_passages: object, _query: str) -> list[tuple[int, float]]:
        return [(1, 9.0), (0, 1.0)]

    monkeypatch.setattr(search_module, "try_duckdb_fts_scores", fake_scores)

    result = search_bundle(load_bundle(tmp_path), "needle", detail="full")

    assert isinstance(result, dict)
    assert result["diagnostics"] == {"engine": "duckdb_fts"}
    rows = result["results"]
    assert isinstance(rows, list)
    typed_rows = cast("list[dict[str, object]]", rows)
    assert [row["body_start_line"] for row in typed_rows] == [2, 1]


def test_search_uses_builtin_scorer_when_local_fts_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_concept(tmp_path / "a.md", "needle")

    def no_fts(_passages: object, _query: str) -> None:
        return None

    monkeypatch.setattr(search_module, "try_duckdb_fts_scores", no_fts)

    result = search_bundle(load_bundle(tmp_path), "needle", detail="full")

    assert isinstance(result, dict)
    assert result["diagnostics"] == {"engine": "builtin_lexical_v1"}


def test_literal_mode_never_attempts_fts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_concept(tmp_path / "a.md", "needle")

    def unexpected_fts(_passages: object, _query: str) -> None:
        msg = "literal search attempted lexical FTS"
        raise AssertionError(msg)

    monkeypatch.setattr(search_module, "try_duckdb_fts_scores", unexpected_fts)

    assert search_bundle(load_bundle(tmp_path), "needle", mode="literal") == (
        "location\tsnippet\na.md#B1\tneedle"
    )
