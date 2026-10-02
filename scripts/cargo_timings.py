#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Run a command, measure wall time, and persist build metadata.

Designed for Cargo commands with `--timings`, but intentionally generic so it
can also measure supporting build steps. The JSON report is written even when
the command fails, and the wrapped command's exit code is preserved.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path


def _git_head() -> str | None:
    try:
        return subprocess.check_output(  # noqa: S603 - fixed Git probe, no shell
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - standard Git executable
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _command_version(command: str) -> str | None:
    try:
        return subprocess.check_output(  # noqa: S603 - wrapper intentionally executes its CLI command
            [command, "--version"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _cargo_summary(timings_dir: Path) -> dict[str, object] | None:
    timing = timings_dir / "cargo-timing.html"
    if not timing.exists():
        return None
    source = timing.read_text(errors="replace")
    match = re.search(r"const UNIT_DATA = (\[.*?\n\]);", source, re.DOTALL)
    if match is None:
        return None
    units = json.loads(match.group(1))
    by_crate: defaultdict[str, float] = defaultdict(float)
    for unit in units:
        by_crate[unit["name"]] += float(unit.get("duration") or 0)
    top_crates = [
        {"crate": name, "compile_seconds": round(duration, 3)}
        for name, duration in sorted(by_crate.items(), key=lambda item: item[1], reverse=True)[:15]
    ]
    duration = re.search(r"^DURATION = ([0-9.]+);$", source, re.MULTILINE)
    return {
        "cargo_duration_seconds": float(duration.group(1)) if duration else None,
        "unit_count": len(units),
        "aggregate_unit_seconds": round(sum(by_crate.values()), 3),
        "top_crates": top_crates,
    }


def main() -> int:
    """Measure one command and persist reproducible build metadata."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    started = time.time()
    monotonic_started = time.monotonic()
    try:
        completed = subprocess.run(command, check=False)  # noqa: S603 - explicit wrapped CLI command
        returncode = completed.returncode
    except OSError as exc:
        sys.stderr.write(f"failed to start {command[0]!r}: {exc}\n")
        returncode = 127
    elapsed = time.monotonic() - monotonic_started

    target_dir = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    timings_dir = target_dir / "cargo-timings"
    timing_files = []
    if timings_dir.exists():
        timing_files = sorted(str(path) for path in timings_dir.glob("*") if path.is_file())

    report = {
        "schema_version": 2,
        "label": args.label,
        "command": command,
        "command_shell": shlex.join(command),
        "returncode": returncode,
        "elapsed_seconds": round(elapsed, 3),
        "started_unix": round(started, 3),
        "git_head": _git_head(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "tool_version": _command_version(command[0]),
        "cargo_target_dir": str(target_dir),
        "cargo_timing_files": timing_files,
        "cargo_summary": _cargo_summary(timings_dir),
        "github_runner": {
            "os": os.environ.get("RUNNER_OS"),
            "arch": os.environ.get("RUNNER_ARCH"),
            "name": os.environ.get("RUNNER_NAME"),
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    sys.stdout.write(
        f"[build-timing] {args.label}: {elapsed:.3f}s (exit {returncode}); report={args.output}\n"
    )
    if report["cargo_summary"]:
        for item in report["cargo_summary"]["top_crates"][:5]:
            sys.stdout.write(
                f"[build-timing] crate {item['crate']}: {item['compile_seconds']:.3f}s aggregate\n"
            )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
