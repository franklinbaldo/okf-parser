#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Prove each wheel installs exactly one unified `okf-parser` executable.

RFC 0003 makes the Rust engine an implementation detail of the single
`okf-parser` distribution: one wheel, one command. A second script in the
wheel's scripts payload -- or none at all -- means the packaging drifted back
toward a companion executable, and the failure only surfaces when a consumer's
`okf-parser` resolves to the wrong binary.

The check reads the archive member list, so it works for cross-built wheels on
any host, without installing or running them. It was inline shell in two
workflows; as a PEP 723 script it is one definition, testable off CI, and takes
every wheel at once instead of being wrapped in a bash loop.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

_SCRIPT_SUFFIXES = (".data/scripts/okf-parser", ".data/scripts/okf-parser.exe")


def wheel_scripts(wheel_path: Path) -> list[str]:
    """Return the wheel's `okf-parser` script entries, in archive order."""
    with zipfile.ZipFile(wheel_path) as archive:
        return [name for name in archive.namelist() if name.endswith(_SCRIPT_SUFFIXES)]


def check(wheel_path: Path) -> str | None:
    """Return a problem description, or None when the wheel carries one script."""
    try:
        scripts = wheel_scripts(wheel_path)
    except (OSError, zipfile.BadZipFile) as exc:
        return f"{wheel_path.name}: cannot read wheel: {exc}"
    if len(scripts) != 1:
        return f"{wheel_path.name}: expected one unified okf-parser script, found {scripts}"
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: fail unless every given wheel has one okf-parser script."""
    parser = argparse.ArgumentParser(description="Verify the wheel scripts payload.")
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args(argv)

    problems = [problem for wheel in args.wheels if (problem := check(wheel)) is not None]
    for problem in problems:
        sys.stderr.write(f"{problem}\n")
    if problems:
        return 1
    for wheel in args.wheels:
        sys.stdout.write(f"{wheel.name}: one unified okf-parser script\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
