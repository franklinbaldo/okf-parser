#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Prove a built wheel's executable does not link against a DuckDB library.

The Python distribution depends on `duckdb` and runs its DuckDB export
through that, so the packaged Rust executable has no reason to carry a
second DuckDB. When it did, every wheel either paid a full C++ amalgamation
build or shipped a dynamic library that had to be vendored and rpath-patched
per platform -- and 0.42.6 published macOS and Windows wheels that linked a
libduckdb they did not ship, failing at startup with a loader error.

A dynamically linked executable must name its libraries in its own import
tables, so the library's file name appears verbatim in the binary. Scanning
for those names catches the regression on every platform and architecture,
including cross-built wheels, without needing ldd, otool, dumpbin or a
matching host to run the binary on.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

_SCRIPT_SUFFIXES = (".data/scripts/okf-parser", ".data/scripts/okf-parser.exe")
_FORBIDDEN = (b"libduckdb.so", b"libduckdb.dylib", b"duckdb.dll")
_LIBRARY_SUFFIXES = (".so", ".dylib", ".dll", ".a", ".lib")


def _executables(archive: zipfile.ZipFile) -> list[str]:
    return [name for name in archive.namelist() if name.endswith(_SCRIPT_SUFFIXES)]


def check(wheel_path: Path) -> list[str]:
    """Return a problem description per offending entry, empty when clean."""
    problems: list[str] = []
    with zipfile.ZipFile(wheel_path) as archive:
        vendored = [
            name
            for name in archive.namelist()
            if "duckdb" in name.rsplit("/", 1)[-1].lower() and name.endswith(_LIBRARY_SUFFIXES)
        ]
        problems.extend(f"{wheel_path.name}: ships a DuckDB library at {name}" for name in vendored)

        executables = _executables(archive)
        if not executables:
            problems.append(f"{wheel_path.name}: no okf-parser executable found")
        for name in executables:
            payload = archive.read(name)
            found = [token.decode() for token in _FORBIDDEN if token in payload]
            if found:
                problems.append(f"{wheel_path.name}: {name} links {', '.join(found)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: fail when any given wheel links or ships DuckDB."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args(argv)

    problems = [problem for wheel in args.wheels for problem in check(wheel)]
    for problem in problems:
        sys.stderr.write(f"{problem}\n")
    if problems:
        return 1
    for wheel in args.wheels:
        sys.stdout.write(f"{wheel.name}: no DuckDB linkage\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
