"""Shared RFC 0016 search cases for future cross-runtime parity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import okf_parser.search as search_module
from okf_parser.bundle import load_bundle
from okf_parser.search import search_bundle


def test_shared_search_conformance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_fts(_passages: object, _query: str) -> None:
        return None

    monkeypatch.setattr(search_module, "try_duckdb_fts_scores", no_fts)
    cases = json.loads(
        (Path(__file__).parents[1] / "conformance" / "search.json").read_text(encoding="utf-8")
    )
    for index, case in enumerate(cases):
        root = tmp_path / f"case-{index}"
        for relative, content in case["files"].items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        bundle = load_bundle(root, case.get("exclude", []))
        result = search_bundle(bundle, **case["request"])

        assert result == case["expected"], case["name"]
