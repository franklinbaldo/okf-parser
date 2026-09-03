"""Tests for the RFC 0016 Phase 1A offline search core."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

import okf_parser.search as search_module
from okf_parser.bundle import load_bundle
from okf_parser.search import SearchError, search_bundle

if TYPE_CHECKING:
    from pathlib import Path


def _result_rows(result: str | dict[str, object]) -> list[dict[str, object]]:
    """Return the structured result rows of a full-detail search response."""
    assert isinstance(result, dict)
    rows = result["results"]
    assert isinstance(rows, list)
    return cast("list[dict[str, object]]", rows)


@pytest.fixture(autouse=True)
def _disable_optional_fts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Phase 1A core tests independent of host extension availability."""

    def no_fts(_passages: object, _query: str) -> None:
        return None

    monkeypatch.setattr(search_module, "try_duckdb_fts_scores", no_fts)


def _write_concept(path: Path, concept_type: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: {concept_type}\n---\n{body}", encoding="utf-8")


def test_literal_search_uses_unicode_casefold_and_exact_body_location(tmp_path: Path) -> None:
    _write_concept(tmp_path / "legal.md", "Tese", "# Título\nA Straße está aberta.\n")

    result = search_bundle(load_bundle(tmp_path), "STRASSE", mode="literal")

    assert result == "location\tsnippet\nlegal.md#B2\tA Straße está aberta."


def test_context_expands_after_selection_and_preserves_blank_lines(tmp_path: Path) -> None:
    _write_concept(tmp_path / "legal.md", "Tese", "alpha\n\nbeta\tgama\nlast")

    result = search_bundle(
        load_bundle(tmp_path),
        "beta",
        mode="literal",
        context=1,
        detail="full",
    )

    rows = _result_rows(result)
    assert rows[0]["body_start_line"] == 2
    assert rows[0]["body_end_line"] == 4
    assert rows[0]["location"] == "legal.md#B2-B4"
    assert rows[0]["text"] == "\nbeta\tgama\nlast"


def test_compact_context_only_rewrites_tabs_and_line_breaks(tmp_path: Path) -> None:
    _write_concept(tmp_path / "legal.md", "Tese", "alpha\n\nbeta\tgama\n  last  ")

    result = search_bundle(
        load_bundle(tmp_path),
        "beta",
        mode="literal",
        context=1,
    )

    assert result == "location\tsnippet\nlegal.md#B2-B4\t beta gama   last  "


def test_compact_location_escapes_percent_and_hash(tmp_path: Path) -> None:
    _write_concept(tmp_path / "100%#case.md", "Tese", "needle")

    result = search_bundle(load_bundle(tmp_path), "needle", mode="literal")

    assert result == "location\tsnippet\n100%25%23case.md#B1\tneedle"


def test_filters_use_authored_type_and_positive_bundle_path_pattern(tmp_path: Path) -> None:
    _write_concept(tmp_path / "legal" / "a.md", "Tese", "needle A")
    _write_concept(tmp_path / "legal" / "b.md", "tese", "needle B")
    _write_concept(tmp_path / "other" / "c.md", "Tese", "needle C")

    result = search_bundle(
        load_bundle(tmp_path),
        "needle",
        mode="literal",
        concept_type="Tese",
        path_glob="legal/*.md",
    )

    assert result == "location\tsnippet\nlegal/a.md#B1\tneedle A"


def test_positive_path_pattern_reuses_recursive_rfc_0004_matching(tmp_path: Path) -> None:
    _write_concept(tmp_path / "a" / "deep" / "one.md", "Tese", "needle one")
    _write_concept(tmp_path / "b" / "two.md", "Tese", "needle two")

    result = search_bundle(
        load_bundle(tmp_path),
        "needle",
        mode="literal",
        path_glob="a/**/*.md",
    )

    assert result == "location\tsnippet\na/deep/one.md#B1\tneedle one"


def test_literal_ties_use_raw_path_then_body_coordinates(tmp_path: Path) -> None:
    _write_concept(tmp_path / "b.md", "Tese", "needle second\nneedle third")
    _write_concept(tmp_path / "a.md", "Tese", "needle first")

    result = search_bundle(load_bundle(tmp_path), "needle", mode="literal")

    assert result == (
        "location\tsnippet\na.md#B1\tneedle first\nb.md#B1\tneedle second\nb.md#B2\tneedle third"
    )


def test_builtin_lexical_ranking_is_deterministic_and_reported(tmp_path: Path) -> None:
    _write_concept(tmp_path / "a.md", "Tese", "alpha alpha beta")
    _write_concept(tmp_path / "b.md", "Tese", "alpha beta gamma delta")
    _write_concept(tmp_path / "c.md", "Tese", "unrelated")

    first = search_bundle(load_bundle(tmp_path), "alpha beta", detail="full")
    second = search_bundle(load_bundle(tmp_path), "alpha beta", detail="full")

    assert first == second
    assert isinstance(first, dict)
    assert first["diagnostics"] == {"engine": "builtin_lexical_v1"}
    rows = _result_rows(first)
    assert [row["path"] for row in rows] == ["a.md", "b.md"]
    scores = [row["score"] for row in rows]
    assert all(isinstance(score, float) for score in scores)
    assert cast("float", scores[0]) > cast("float", scores[1]) > 0


def test_limit_is_applied_before_context_expansion(tmp_path: Path) -> None:
    _write_concept(tmp_path / "a.md", "Tese", "before\nneedle\nafter")
    _write_concept(tmp_path / "b.md", "Tese", "needle")

    result = search_bundle(
        load_bundle(tmp_path),
        "needle",
        mode="literal",
        limit=1,
        context=1,
    )

    assert result == "location\tsnippet\na.md#B1-B3\tbefore needle after"


def test_empty_body_returns_only_the_normative_header(tmp_path: Path) -> None:
    _write_concept(tmp_path / "empty.md", "Tese", "")

    result = search_bundle(load_bundle(tmp_path), "needle")

    assert result == "location\tsnippet"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"query": "   "}, "query must not be empty"),
        ({"query": "x", "limit": 0}, "limit must be at least 1"),
        ({"query": "x", "context": -1}, "context must be non-negative"),
        ({"query": "x", "mode": "regex"}, "unsupported search mode: regex"),
        ({"query": "x", "detail": "verbose"}, "unsupported search detail: verbose"),
        ({"query": "x", "mode": "vector"}, "vector retrieval is not configured"),
        ({"query": "x", "mode": "hybrid"}, "hybrid retrieval is not configured"),
        ({"query": "x", "profile": "named"}, "profile is not configured: named"),
        ({"query": "x", "path_glob": "!a.md"}, "path_glob does not support negation"),
        (
            {"query": "x", "path_glob": "# comment"},
            "path_glob must be one non-empty positive path pattern",
        ),
    ],
)
def test_search_request_validation(
    tmp_path: Path,
    kwargs: dict[str, Any],
    message: str,
) -> None:
    _write_concept(tmp_path / "a.md", "Tese", "x")

    with pytest.raises(SearchError, match=message):
        search_bundle(load_bundle(tmp_path), **kwargs)
