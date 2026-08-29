#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Print the version the release is built around.

Five workflow steps used to inline the same
`python -c 'import tomllib; ...["project"]["version"]'`, each one reaching for
whatever `python` happened to be on the runner. That is the one number every
artifact name, glob and contract check is derived from, so a copy that drifts
or an interpreter that is not there fails the release late and obscurely.

Reading it through one PEP 723 script gives the release a single definition and
one interpreter policy: `uv run --script` resolves its own environment, so no
step depends on the runner's ambient Python.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path


class VersionError(ValueError):
    """Report a missing or malformed project version."""


def project_version(pyproject: Path) -> str:
    """Return `project.version` from the given pyproject.toml."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        message = f"cannot read {pyproject}: {exc}"
        raise VersionError(message) from exc
    project = data.get("project")
    if not isinstance(project, dict):
        message = f"{pyproject} has no [project] table"
        raise VersionError(message)
    version = project.get("version")
    if not isinstance(version, str) or not version:
        message = f"{pyproject} has no non-empty project.version"
        raise VersionError(message)
    return version


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: print the project version on one line."""
    parser = argparse.ArgumentParser(description="Print project.version from pyproject.toml.")
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args(argv)

    try:
        version = project_version(args.pyproject)
    except VersionError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"{version}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
