"""Structural gates for the agentic benchmark registries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from okf_parser import load_bundle

REPOSITORY = Path(__file__).resolve().parents[1]
AGENTIC = REPOSITORY / "benchmarks" / "agentic"
RIVALS = REPOSITORY / "benchmarks" / "rivals"


def _concepts(path: Path, concept_type: str) -> list[dict[str, Any]]:
    frame = load_bundle(path).concepts.execute()
    result = []
    for row in frame.itertuples():
        data = json.loads(row.frontmatter_json)
        if data.get("type") == concept_type:
            result.append(data)
    return result


def _flag(value: object) -> bool:
    return value is True or str(value).strip().lower() == "true"


def test_agentic_configuration_is_authored_as_okf_not_json() -> None:
    authored_json = list(AGENTIC.rglob("*.json"))
    assert authored_json == []


def test_first_round_fixes_exactly_one_cline_harness() -> None:
    enabled = [
        item
        for item in _concepts(AGENTIC / "harnesses", "BenchmarkHarness")
        if _flag(item.get("enabled"))
    ]
    assert len(enabled) == 1
    assert enabled[0]["harness_id"] == "cline"
    assert enabled[0]["provider"] == "openrouter"
    assert enabled[0]["version"] == "3.0.60"


def test_first_round_tasks_are_large_and_include_conversion() -> None:
    tasks = _concepts(AGENTIC / "tasks", "BenchmarkTask")
    assert len(tasks) >= 9
    assert all(int(task["fixture_size"]) >= 1000 for task in tasks)
    conversion = next(task for task in tasks if task["task_id"] == "json-to-okf")
    assert conversion["fixture_kind"] == "json-records"
    assert int(conversion["fixture_size"]) >= 1000


def test_agentic_rivals_are_declarative_and_pinned() -> None:
    enabled = [
        item
        for item in _concepts(RIVALS, "Rival")
        if _flag(item.get("agentic_enabled"))
    ]
    names = {item["title"] for item in enabled}
    assert {"okf-parser", "okf-generator", "kbforge-okfquery"} <= names
    assert len(enabled) >= 7
    for rival in enabled:
        assert rival["package"]
        assert rival["agentic_version"]
        assert rival["agentic_executable"]
        assert rival["agentic_instruction"]


def test_benchmark_run_schema_records_usage_and_hashes() -> None:
    schema = (REPOSITORY / "docs" / "types" / "benchmarkrun.schema.sql").read_text(
        encoding="utf-8"
    )
    for column in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_usd",
        "answer_sha256",
        "expected_sha256",
    ):
        assert column in schema
