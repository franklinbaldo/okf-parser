# /// script
# requires-python = ">=3.12"
# ///
"""Validate the deterministic agent-token benchmark result contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

_EXPECTED_RESULTS = 18
_EXPECTED_ROUNDS = 2
_EXPECTED_STRATEGIES = {"direct-markdown", "generic-retrieval", "okf-parser"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    return parser


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        message = f"{label} must be a JSON object"
        raise TypeError(message)
    return cast("dict[str, object]", value)


def _sequence(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        message = f"{label} must be a list of JSON objects"
        raise TypeError(message)
    return cast("list[dict[str, object]]", value)


def _validate_measurement(payload: dict[str, object]) -> None:
    if payload.get("schema") != "okf-agent-token-cost-v1":
        message = "unexpected benchmark schema"
        raise ValueError(message)

    measurement = _mapping(payload.get("measurement"), "measurement")
    if measurement.get("primary_metric") != "cumulative agent input/context tokens":
        message = "primary metric is not cumulative agent input/context tokens"
        raise ValueError(message)
    if measurement.get("mode") != "deterministic-context-trace":
        message = "smoke result must be a deterministic context trace"
        raise ValueError(message)


def _validate_diagnostics(payload: dict[str, object]) -> None:
    diagnostics = _mapping(payload.get("diagnostics"), "diagnostics")
    storage = _mapping(diagnostics.get("storage_size"), "storage_size")
    full_tokens = _mapping(
        diagnostics.get("full_representation_tokens"),
        "full_representation_tokens",
    )
    if int(storage.get("authored_markdown_bytes", 0)) <= 0:
        message = "authored Markdown storage diagnostic is missing"
        raise ValueError(message)
    if int(full_tokens.get("authored_markdown", 0)) <= 0:
        message = "authored Markdown token diagnostic is missing"
        raise ValueError(message)
    if int(full_tokens.get("okf_canonical_projection", 0)) <= 0:
        message = "OKF canonical token diagnostic is missing"
        raise ValueError(message)


def _validate_results(payload: dict[str, object]) -> None:
    strategy_aggregates = _sequence(payload.get("strategy_aggregates"), "strategy_aggregates")
    strategies = {str(item["strategy"]) for item in strategy_aggregates}
    if strategies != _EXPECTED_STRATEGIES:
        message = f"unexpected strategies: {sorted(strategies)}"
        raise ValueError(message)

    results = _sequence(payload.get("results"), "results")
    if len(results) != _EXPECTED_RESULTS:
        message = f"expected {_EXPECTED_RESULTS} task/strategy results, got {len(results)}"
        raise ValueError(message)
    if any(int(item["runs"]) != _EXPECTED_ROUNDS for item in results):
        message = "smoke run must contain two repetitions per task/strategy"
        raise ValueError(message)
    if any("input_tokens_reduction_vs_direct" not in item for item in results):
        message = "direct Markdown comparison is missing"
        raise ValueError(message)
    if any(float(item["success_rate"]) not in {0.0, 1.0} for item in results):
        message = "deterministic evidence success must be binary"
        raise ValueError(message)
    if any(
        item["strategy"] != "direct-markdown" and float(item["success_rate"]) != 1.0
        for item in results
    ):
        message = "a retrieval strategy failed the deterministic evidence oracle"
        raise ValueError(message)


def main() -> None:
    """Validate schema, metrics, strategies, and deterministic evidence quality."""
    args = _parser().parse_args()
    payload = _mapping(json.loads(args.result.read_text(encoding="utf-8")), "result")
    _validate_measurement(payload)
    _validate_diagnostics(payload)
    _validate_results(payload)


if __name__ == "__main__":
    main()
