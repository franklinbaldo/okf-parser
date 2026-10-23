#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Create a repeatable OKF corpus and benchmark native binaries."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path


def prepare(root: Path, count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        path = root / f"note-{index:04d}.md"
        path.write_text(
            "---\n"
            "type: Note\n"
            f"sequence: {index}\n"
            f"group: g{index % 20}\n"
            "---\n\n"
            f"# Note {index}\n\n"
            f"Retry budget observation {index}. "
            f"Shared token alpha-{index % 37} beta-{index % 13}.\n"
        )


def timed(command: list[str], repeats: int) -> dict[str, float]:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
        samples.append(time.perf_counter() - started)
    return {
        "median_seconds": round(statistics.median(samples), 6),
        "min_seconds": round(min(samples), 6),
        "max_seconds": round(max(samples), 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--binary", action="append", default=[])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.prepare:
        prepare(args.fixture, args.count)

    if not args.binary:
        return 0
    if args.output is None:
        parser.error("--output is required with --binary")

    results = {}
    for item in args.binary:
        label, raw_path = item.split("=", 1)
        binary = Path(raw_path)
        commands = {
            "parse_inventory": [str(binary), "inventory", str(args.fixture)],
            "search": [
                str(binary),
                "search",
                str(args.fixture),
                "retry budget",
                "--limit",
                "20",
            ],
            "sql": [
                str(binary),
                "sql",
                str(args.fixture),
                "SELECT count(*) AS concepts FROM concepts",
            ],
        }
        results[label] = {
            "binary": str(binary),
            "size_bytes": binary.stat().st_size,
            "benchmarks": {
                name: timed(command, args.repeats) for name, command in commands.items()
            },
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
