#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Fetch the official prebuilt libduckdb the binary links in CI and releases.

Compiling DuckDB into the binary (the `bundled` feature, still the default
for source builds) costs about half an hour of C++ per build. Releases and CI
link the library DuckDB itself publishes instead: this script downloads it for
one Rust target, checks it against a pinned SHA-256, and extracts it under
`target/libduckdb/<target>/`, where `DUCKDB_LIB_DIR` points the build at it.
A macOS universal library is cut down to the target's own slice.

The pins follow the `duckdb` crate in Cargo.lock: crate `1.MMmmpp.x` wraps
DuckDB `MM.mm.pp`, and a crate bump without new pins fails here, loudly.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import re
import struct
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

DUCKDB_VERSION = "1.5.4"
_URL = "https://github.com/duckdb/duckdb/releases/download/v{version}/{archive}"


@dataclass(frozen=True, slots=True)
class Archive:
    """One official libduckdb release archive and the library inside it."""

    name: str
    sha256: str
    library: str
    #: The Mach-O CPU type to keep from a universal library, if any.
    macho_cpu: int | None = None


_ARM64 = 0x0100000C
_X86_64 = 0x01000007
ARCHIVES: dict[str, Archive] = {
    "x86_64-unknown-linux-gnu": Archive(
        "libduckdb-linux-amd64.zip",
        "838d98a85e697bab9935010c88a8c67d3312ccedcab4cb4a0ba01da65113bb70",
        "libduckdb.so",
    ),
    "aarch64-unknown-linux-gnu": Archive(
        "libduckdb-linux-arm64.zip",
        "7e154fd14d5b7afd7c9f36071000bf4badd3fa57a125cf7dc596f2c5ad82c1b2",
        "libduckdb.so",
    ),
    "x86_64-apple-darwin": Archive(
        "libduckdb-osx-universal.zip",
        "3f3c52970ad1407ec5037062e1a5e575b24bd5b993c889f89fe5876eff47782c",
        "libduckdb.dylib",
        _X86_64,
    ),
    "aarch64-apple-darwin": Archive(
        "libduckdb-osx-universal.zip",
        "3f3c52970ad1407ec5037062e1a5e575b24bd5b993c889f89fe5876eff47782c",
        "libduckdb.dylib",
        _ARM64,
    ),
    "x86_64-pc-windows-msvc": Archive(
        "libduckdb-windows-amd64.zip",
        "74e73afd3b010c6f310e14a961fff679f876952bc196f82584b0e2e76d11a91f",
        "duckdb.dll",
    ),
}
_HEADERS = ("duckdb.h",)
#: What the linker needs besides the library itself (the MSVC import library).
_LINK_ONLY = ("duckdb.lib",)


class FetchError(RuntimeError):
    """The pinned library could not be fetched or does not match its pin."""


def host_target() -> str:
    """The Rust target triple of this machine, among the ones pinned."""
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    system = {"Linux": "unknown-linux-gnu", "Darwin": "apple-darwin", "Windows": "pc-windows-msvc"}
    return f"{arch.get(machine, machine)}-{system.get(platform.system(), platform.system())}"


def crate_duckdb_version(lockfile: Path) -> str:
    """The DuckDB version the locked `duckdb` crate wraps."""
    text = lockfile.read_text(encoding="utf-8")
    match = re.search(r'name = "duckdb"\nversion = "1\.(\d+)\.\d+"', text)
    if match is None:
        msg = f"{lockfile} does not lock the duckdb crate"
        raise FetchError(msg)
    encoded = int(match.group(1))
    return f"{encoded // 10000}.{encoded // 100 % 100}.{encoded % 100}"


def thin(library: bytes, cpu: int) -> bytes:
    """The `cpu` slice of a universal (fat) Mach-O library, or `library` as is."""
    if library[:4] != b"\xca\xfe\xba\xbe":
        return library
    (count,) = struct.unpack(">I", library[4:8])
    for index in range(count):
        entry = library[8 + index * 20 : 28 + index * 20]
        slice_cpu, _subtype, offset, size, _align = struct.unpack(">IIIII", entry)
        if slice_cpu == cpu:
            return library[offset : offset + size]
    msg = f"universal library has no slice for CPU type {cpu:#x}"
    raise FetchError(msg)


def download(url: str) -> bytes:
    """Read `url` whole; honours the usual proxy and CA environment."""
    with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310 - fixed https URL
        return response.read()


def fetch(target: str, destination: Path, payload: bytes | None = None) -> Path:
    """Extract the pinned library for `target` into `destination`; return it."""
    archive = ARCHIVES.get(target)
    if archive is None:
        msg = f"no pinned libduckdb for target {target!r}; known: {', '.join(ARCHIVES)}"
        raise FetchError(msg)
    if payload is None:
        payload = download(_URL.format(version=DUCKDB_VERSION, archive=archive.name))
    digest = hashlib.sha256(payload).hexdigest()
    if digest != archive.sha256:
        msg = f"{archive.name}: sha256 {digest} does not match the pin {archive.sha256}"
        raise FetchError(msg)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
        for member in (*_HEADERS, *_LINK_ONLY):
            if member in zipped.namelist():
                (destination / member).write_bytes(zipped.read(member))
        library = zipped.read(archive.library)
    if archive.macho_cpu is not None:
        library = thin(library, archive.macho_cpu)
    path = destination / archive.library
    path.write_bytes(library)
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=None, help="Rust target triple (default: this host)")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument(
        "--github-env",
        action="store_true",
        help="export DUCKDB_LIB_DIR, DUCKDB_INCLUDE_DIR and DUCKDB_LIBRARY to $GITHUB_ENV",
    )
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="also put the library on the loader path, for tests run from target/",
    )
    return parser


def check_pins(lockfile: Path) -> None:
    """Refuse pins for a DuckDB other than the one the locked crate wraps."""
    locked = crate_duckdb_version(lockfile)
    if locked != DUCKDB_VERSION:
        msg = f"Cargo.lock wraps DuckDB {locked}, but the pins are for {DUCKDB_VERSION}"
        raise FetchError(msg)


def main(argv: list[str] | None = None) -> int:
    """Fetch, verify and extract; optionally export the build environment."""
    arguments = _parser().parse_args(argv)
    root = arguments.root.resolve()
    target = arguments.target or host_target()
    try:
        check_pins(root / "Cargo.lock")
        library = fetch(target, root / "target" / "libduckdb" / target)
    except (FetchError, OSError) as exc:
        sys.stderr.write(f"fetch_libduckdb: {exc}\n")
        return 1
    directory = library.parent
    exports = {
        "DUCKDB_LIB_DIR": str(directory),
        "DUCKDB_INCLUDE_DIR": str(directory),
        "DUCKDB_LIBRARY": str(library),
    }
    if arguments.runtime:
        variable = {"apple-darwin": "DYLD_LIBRARY_PATH", "pc-windows-msvc": "PATH"}
        name = next((v for k, v in variable.items() if target.endswith(k)), "LD_LIBRARY_PATH")
        previous = os.environ.get(name)
        exports[name] = str(directory) + (os.pathsep + previous if previous else "")
    if arguments.github_env:
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in exports.items())
    for key, value in exports.items():
        sys.stdout.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
