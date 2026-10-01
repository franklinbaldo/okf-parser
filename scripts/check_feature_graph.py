#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Assert that Rust capability features activate only their intended dependency graph."""

from __future__ import annotations

import json
import subprocess


SCENARIOS = {
    "engine": {
        "features": [],
        "present": {"okf-engine"},
        "absent": {"okf-db", "duckdb", "rmcp", "axum", "tokio"},
    },
    "sql": {
        "features": ["sql"],
        "present": {"okf-engine", "okf-db", "duckdb"},
        "absent": {"rmcp", "axum", "schemars"},
    },
    "mcp": {
        "features": ["mcp"],
        "present": {"okf-engine", "rmcp", "axum", "tokio"},
        "absent": {"okf-db", "duckdb"},
    },
    "full": {
        "features": ["full"],
        "present": {"okf-engine", "okf-db", "duckdb", "rmcp", "axum", "tokio"},
        "absent": set(),
    },
}


def reachable_package_names(metadata: dict[str, object]) -> set[str]:
    packages = {package["id"]: package["name"] for package in metadata["packages"]}
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    root = next(package["id"] for package in metadata["packages"] if package["name"] == "okf-core")
    pending = [root]
    seen: set[str] = set()
    while pending:
        package_id = pending.pop()
        if package_id in seen:
            continue
        seen.add(package_id)
        pending.extend(dep["pkg"] for dep in nodes[package_id]["deps"])
    return {packages[package_id] for package_id in seen}


def metadata(features: list[str]) -> dict[str, object]:
    command = [
        "cargo",
        "metadata",
        "--format-version",
        "1",
        "--no-default-features",
        "--manifest-path",
        "rust-core/Cargo.toml",
    ]
    if features:
        command.extend(["--features", ",".join(features)])
    return json.loads(subprocess.check_output(command, text=True))


def main() -> int:
    failed = False
    for name, scenario in SCENARIOS.items():
        packages = reachable_package_names(metadata(scenario["features"]))
        missing = scenario["present"] - packages
        unexpected = scenario["absent"] & packages
        print(
            f"{name}: {len(packages)} reachable packages; "
            f"features={','.join(scenario['features']) or '(none)'}"
        )
        if missing:
            failed = True
            print(f"  missing: {', '.join(sorted(missing))}")
        if unexpected:
            failed = True
            print(f"  unexpected: {', '.join(sorted(unexpected))}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
