#!/usr/bin/env python3
"""Plan the task/harness Actions matrix and selected tool list."""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ids(name: str, key: str) -> list[str]:
    """Read registry ids in declaration order."""
    data = json.loads((HERE / name).read_text(encoding="utf-8"))
    return [item["id"] for item in data[key]]


def selected(value: str, available: list[str]) -> list[str]:
    """Resolve `all` or a comma-separated selection and reject unknown ids."""
    if value == "all":
        return available
    wanted = [part.strip() for part in value.split(",") if part.strip()]
    unknown = sorted(set(wanted) - set(available))
    if unknown:
        msg = f"unknown selection: {unknown}; available={available}"
        raise SystemExit(msg)
    return wanted


def main() -> None:
    """Print one machine-readable planning component."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="all")
    parser.add_argument("--tools", default="all")
    parser.add_argument("--harnesses", default="all")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--mode", choices=["matrix", "tools", "repetitions"], required=True)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10:
        raise SystemExit("repetitions must be between 1 and 10")

    tasks = selected(args.tasks, ids("tasks.json", "tasks"))
    tools = selected(args.tools, ids("tools.json", "tools"))
    harnesses = selected(args.harnesses, ids("harnesses.json", "harnesses"))

    if args.mode == "matrix":
        include = [
            {"task": task, "harness": harness}
            for task, harness in itertools.product(tasks, harnesses)
        ]
        print(json.dumps({"include": include}, separators=(",", ":")))
    elif args.mode == "tools":
        print(json.dumps(tools, separators=(",", ":")))
    else:
        print(args.repetitions)


if __name__ == "__main__":
    main()
