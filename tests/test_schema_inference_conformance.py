"""Cross-runtime conformance tests for exact schema scalar inference."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from okf_parser.schema_lexemes import CastKind, can_classify_as, classify_lexemes

_FIXTURE_PATH = Path(__file__).parents[1] / "conformance" / "schema-inference.json"


def _section(name: str) -> list[dict[str, object]]:
    payload = cast("dict[str, object]", json.loads(_FIXTURE_PATH.read_text(encoding="utf-8")))
    return cast("list[dict[str, object]]", payload[name])


def test_shared_schema_classification_corpus() -> None:
    for case in _section("classification"):
        values = cast("list[str]", case["values"])
        expected = cast("CastKind", case["kind"])
        assert classify_lexemes(values) == expected, case["id"]


def test_shared_schema_cast_corpus() -> None:
    for case in _section("casts"):
        values = cast("list[str]", case["values"])
        kind = cast("CastKind", case["kind"])
        expected = cast("bool", case["valid"])
        assert can_classify_as(values, kind) is expected, case["id"]
