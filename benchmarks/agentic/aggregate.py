#!/usr/bin/env python3
"""Aggregate per-trial JSONL records into one immutable round artifact."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--jsonl", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()

    records: list[dict[str, Any]] = []
    for path in sorted(args.root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))

    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.jsonl.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = (record["task_id"], record["tool"]["id"], record["agent"]["harness"])
        grouped[key].append(record)

    lines = [
        "# Agentic capability benchmark",
        "",
        f"Trials: **{len(records)}**",
        "",
        "| task | tool | harness | passes | trials | pass rate | median successful seconds |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for (task, tool, harness), group in sorted(grouped.items()):
        successes = [r for r in group if r["result"]["status"] == "success"]
        durations = [float(r["wall_seconds"]) for r in successes]
        med = f"{median(durations):.2f}" if durations else "—"
        lines.append(
            f"| {task} | {tool} | {harness} | {len(successes)} | {len(group)} | "
            f"{len(successes) / len(group):.0%} | {med} |"
        )
    lines.extend([
        "",
        "A failed trial retains its failure class in the JSONL. The baseline row uses no OKF-specific tool.",
        "Raw transcripts and executable-invocation logs remain separate artifacts.",
    ])
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
