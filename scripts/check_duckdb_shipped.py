#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Prove a built wheel ships the libduckdb its executable links, beside it.

Release binaries link DuckDB's official prebuilt library rather than compiling
DuckDB in (RFC 0024 phase 4). That is the arrangement 0.42.6 got wrong: its
macOS and Windows wheels linked a libduckdb they did not ship and failed at
startup with a loader error. So every wheel must carry exactly the library its
executable names, in the executable's own `.data/scripts/` directory (where
the binary's `$ORIGIN`/`@executable_path` run path and the Windows DLL search
look), and no other copy of DuckDB anywhere.

A dynamically linked executable names its libraries in its own import tables,
so the file name appears verbatim in the binary. Scanning for it works on every
platform and architecture, cross-built wheels included, without ldd, otool,
dumpbin or a matching host to run the binary on.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

_SCRIPT_SUFFIXES = (".data/scripts/okf-parser", ".data/scripts/okf-parser.exe")
_LIBRARIES = (b"libduckdb.so", b"libduckdb.dylib", b"duckdb.dll")
_LIBRARY_SUFFIXES = (".so", ".dylib", ".dll", ".a", ".lib")


def check(wheel_path: Path) -> list[str]:
    """Return a problem description per offending entry, empty when sound."""
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
        executables = [name for name in names if name.endswith(_SCRIPT_SUFFIXES)]
        if len(executables) != 1:
            return [f"{wheel_path.name}: expected one okf-parser executable, found {executables}"]
        executable = executables[0]
        payload = archive.read(executable)
    linked = [token.decode() for token in _LIBRARIES if token in payload]
    if len(linked) != 1:
        return [f"{wheel_path.name}: {executable} should link one libduckdb, found {linked}"]
    expected = executable.rsplit("/", 1)[0] + "/" + linked[0]
    problems: list[str] = []
    if expected not in names:
        problems.append(f"{wheel_path.name}: links {linked[0]} but does not ship {expected}")
    problems.extend(
        f"{wheel_path.name}: ships an unexpected DuckDB library at {name}"
        for name in names
        if name != expected
        and "duckdb" in name.rsplit("/", 1)[-1].lower()
        and name.endswith(_LIBRARY_SUFFIXES)
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: fail unless every wheel ships the library it links."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args(argv)

    problems = [problem for wheel in args.wheels for problem in check(wheel)]
    for problem in problems:
        sys.stderr.write(f"{problem}\n")
    if problems:
        return 1
    for wheel in args.wheels:
        sys.stdout.write(f"{wheel.name}: ships the libduckdb it links\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
