#!/usr/bin/env python3
"""Build the GitHub Actions matrix for an agentic benchmark round."""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ids(name: str, key: str) -> list[str]:
    data = json.loads((HERE / name).read_text(encoding="utf-8"))
    return [item["id"] for item in data[key]]


def selected(value: str, available: list[str]) -> list[str]:
    if value == "all":
        return available
    wanted = [part.strip() for part in value.split(",") if part.strip()]
    unknown = sorted(set(wanted) - set(available))
    if unknown:
        raise SystemExit(f"unknown selection: {unknown}; available={available}")
    return wanted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="all")
    parser.add_argument("--tools", default="all")
    parser.add_argument("--harnesses", default="all")
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10:
        raise SystemExit("repetitions must be between 1 and 10")

    tasks = selected(args.tasks, ids("tasks.json", "tasks"))
    tools = selected(args.tools, ids("tools.json", "tools"))
    harnesses = selected(args.harnesses, ids("harnesses.json", "harnesses"))
    include = [
        {"task": task, "tool": tool, "harness": harness, "repetition": repetition}
        for task, tool, harness, repetition in itertools.product(
            tasks, tools, harnesses, range(1, args.repetitions + 1)
        )
    ]
    print(json.dumps({"include": include}, separators=(",", ":")))


if __name__ == "__main__":
    main()
