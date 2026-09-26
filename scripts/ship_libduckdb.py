#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Put the linked libduckdb into a wheel, next to the `okf-parser` executable.

A release binary links DuckDB's own prebuilt library (`fetch_libduckdb.py`)
and looks for it in its own directory (`$ORIGIN`, `@executable_path`, or
the `.exe`'s folder on Windows; see `rust-core/build.rs`). Installers copy
everything in a wheel's `.data/scripts/` into the same directory, so the
library goes there, and the wheel's RECORD is rewritten to list it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import zipfile
from pathlib import Path

_EXECUTABLES = (".data/scripts/okf-parser", ".data/scripts/okf-parser.exe")


class ShipError(ValueError):
    """The wheel is not a single-executable okf-parser wheel."""


def _record_line(name: str, payload: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(payload)}"


def ship(wheel: Path, library: Path) -> str:
    """Add `library` beside the wheel's executable and return its member name."""
    with zipfile.ZipFile(wheel) as source:
        entries = [(info, source.read(info.filename)) for info in source.infolist()]
    names = [info.filename for info, _ in entries]
    executables = [name for name in names if name.endswith(_EXECUTABLES)]
    if len(executables) != 1:
        msg = f"{wheel.name}: expected one okf-parser executable, found {executables}"
        raise ShipError(msg)
    member = executables[0].rsplit("/", 1)[0] + "/" + library.name
    if member in names:
        msg = f"{wheel.name}: already ships {member}"
        raise ShipError(msg)
    records = [name for name in names if name.endswith(".dist-info/RECORD")]
    if len(records) != 1:
        msg = f"{wheel.name}: expected one RECORD, found {records}"
        raise ShipError(msg)
    payload = library.read_bytes()
    temporary = wheel.with_suffix(".tmp")
    library_info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
    library_info.external_attr = 0o100755 << 16
    library_info.compress_type = zipfile.ZIP_DEFLATED
    shipped = False
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for info, data in entries:
            # The library goes in before the .dist-info files, which stay last.
            if not shipped and ".dist-info/" in info.filename:
                target.writestr(library_info, payload)
                shipped = True
            if info.filename != records[0]:
                target.writestr(info, data)
                continue
            lines = data.decode("utf-8").splitlines()
            rest = [line for line in lines if not line.startswith(records[0] + ",")]
            rest.extend((_record_line(member, payload), f"{records[0]},,"))
            target.writestr(info, ("\n".join(rest) + "\n").encode("utf-8"))
    temporary.replace(wheel)
    return member


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ship the library into every given wheel."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("wheels", nargs="+", type=Path)
    arguments = parser.parse_args(argv)
    try:
        for wheel in arguments.wheels:
            member = ship(wheel, arguments.library)
            sys.stdout.write(f"{wheel.name}: ships {member}\n")
    except (ShipError, OSError, zipfile.BadZipFile) as exc:
        sys.stderr.write(f"ship_libduckdb: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
