#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Render the release dry run's job summary from the artifacts it produced.

The summary is the human-readable face of `manifest.json` and
`registry-state.json`: what was built, how large, which digest, and what the
preflight decided to do with each registry target. It was a heredoc piped into
`$GITHUB_STEP_SUMMARY`, which meant it could only ever be exercised by pushing
to CI -- a KeyError in the table would surface as a failed release step.

As a PEP 723 script it runs the same way off CI, against a downloaded release
tree, and its shape is covered by the tests like any other release helper.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _rows(manifest: dict[str, Any], native_npm: list[str]) -> list[str]:
    rows = [
        f"| `{artifact['path']}` | {artifact['size']} | `{artifact['sha256']}` |"
        for artifact in manifest["artifacts"]
    ]
    rows.extend(
        f"| `native-npm/{filename}` | native companion | verified separately |"
        for filename in native_npm
    )
    return rows


def _preflight(registry: dict[str, Any]) -> list[str]:
    actions = {(item["registry"], item["package"]): item["action"] for item in registry["plan"]}
    return [
        f"| {entry['registry']} | `{entry['package']}` | "
        f"`{entry['state']}` | `{entry['package_exists']}` | "
        f"`{actions[entry['registry'], entry['package']]}` |"
        for entry in registry["entries"]
    ]


def render(release: Path) -> str:
    """Return the Markdown summary for a built release tree."""
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    registry = json.loads((release / "registry-state.json").read_text(encoding="utf-8"))
    native_npm = sorted(path.name for path in (release / "native-npm").glob("*.tgz"))
    lines = [
        f"## Release dry run {manifest['version']}",
        "",
        f"Source: `{manifest['repository']}@{manifest['commit']}`",
        "",
        "| Artifact | Bytes | SHA-256 |",
        "|---|---:|---|",
        *_rows(manifest, native_npm),
        "",
        "## Public registry preflight",
        "",
        "| Registry | Package | State | Package exists | Action |",
        "|---|---|---|---|---|",
        *_preflight(registry),
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: write the Markdown summary to stdout."""
    parser = argparse.ArgumentParser(description="Render the release dry run summary.")
    parser.add_argument("--directory", type=Path, default=Path("release"))
    args = parser.parse_args(argv)

    try:
        summary = render(args.directory)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        sys.stderr.write(f"cannot render the release summary from {args.directory}: {exc!r}\n")
        return 1
    sys.stdout.write(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
