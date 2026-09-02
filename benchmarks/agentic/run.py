#!/usr/bin/env python3
# ruff: noqa
"""Stable entrypoint for the first agentic benchmark round."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from okf_parser import load_bundle

import run_round


def _bool(value: object) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _number(value: object, cast: type[int] | type[float]) -> int | float | None:
    if value in (None, "", "null", "None"):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def build_summary(bundle: Path, destination: Path) -> None:
    """Aggregate BenchmarkRun concepts through okf-parser itself."""
    frame = load_bundle(bundle).concepts.execute()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in frame.itertuples():
        data = json.loads(row.frontmatter_json)
        if data.get("type") == "BenchmarkRun":
            groups[str(data["tool_id"])].append(data)

    order = [run_round.BASELINE_ID, "okf-parser"]
    ordered_tools = sorted(groups, key=lambda item: (order.index(item) if item in order else 99, item))
    lines = [
        "# Agentic capability benchmark",
        "",
        "First round: one fixed Cline harness; baseline first; then one OKF tool at a time; each tool completes every selected task before the next tool starts.",
        "",
        "| tool | passes | trials | pass rate | median successful seconds | median successful tokens | median successful cost USD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for tool in ordered_tools:
        rows = groups[tool]
        passed = [row for row in rows if _bool(row.get("graded")) and row.get("status") == "success"]
        seconds = [float(value) for row in passed if (value := _number(row.get("wall_seconds"), float)) is not None]
        tokens = [int(value) for row in passed if (value := _number(row.get("total_tokens"), int)) is not None]
        costs = [float(value) for row in passed if (value := _number(row.get("cost_usd"), float)) is not None]
        seconds_text = f"{median(seconds):.2f}" if seconds else "—"
        tokens_text = str(int(median(tokens))) if tokens else "—"
        cost_text = f"{median(costs):.4f}" if costs else "—"
        lines.append(
            f"| {tool} | {len(passed)} | {len(rows)} | {len(passed) / len(rows):.0%} | "
            f"{seconds_text} | {tokens_text} | {cost_text} |"
        )

    lines.extend(
        [
            "",
            "Token fields are reported only when Cline/OpenRouter exposes authoritative usage. Missing usage remains missing; it is never replaced with zero or estimated locally.",
            "",
            "The canonical per-trial evidence is the `BenchmarkRun` OKF bundle. Raw transcripts, tool invocation logs and produced answers are referenced artifacts.",
        ]
    )
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    run_round.build_summary = build_summary
    return run_round.main()


if __name__ == "__main__":
    raise SystemExit(main())
