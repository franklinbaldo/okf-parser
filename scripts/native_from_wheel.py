#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Reuse the exact Linux x86_64 executable from a Python wheel in npm-native.

The executable links the libduckdb the wheel ships beside it (RFC 0024
phase 4), so the library travels with it, byte for byte, into the same
`bin/` directory: the binary's `$ORIGIN` run path finds it there.
"""

from __future__ import annotations

import argparse
import hashlib
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

_WHEEL_SUFFIX = ".data/scripts/okf-parser"
_NPM_MEMBER = "package/bin/okf-core"
_LIBRARY = "libduckdb.so"


class ArtifactError(ValueError):
    """Report a malformed wheel or native npm artifact."""

    @classmethod
    def wheel_entries(cls, wheel: Path, names: list[str]) -> ArtifactError:
        """Build an error for a wheel with an invalid executable count."""
        return cls(f"{wheel}: expected one okf-parser executable, found {names}")

    @classmethod
    def wheel_read(cls, wheel: Path, error: Exception) -> ArtifactError:
        """Build an error for an unreadable wheel."""
        return cls(f"cannot read wheel {wheel}: {error}")

    @classmethod
    def npm_member(cls, tarball: Path) -> ArtifactError:
        """Build an error for a malformed npm-native binary member."""
        return cls(f"{tarball}: {_NPM_MEMBER} is not a regular file")

    @classmethod
    def npm_read(cls, tarball: Path, error: Exception) -> ArtifactError:
        """Build an error for an unreadable npm-native tarball."""
        return cls(f"cannot read npm tarball {tarball}: {error}")

    @classmethod
    def missing_library(cls, wheel: Path) -> ArtifactError:
        """Build an error for a wheel that does not ship its libduckdb."""
        return cls(f"{wheel}: does not ship {_LIBRARY} beside okf-parser")

    @classmethod
    def mismatch(cls, wheel_sha256: str, npm_sha256: str) -> ArtifactError:
        """Build an error when wheel and npm executables are not identical."""
        return cls(f"native executable mismatch: wheel={wheel_sha256} npm={npm_sha256}")


def wheel_payload(wheel: Path) -> tuple[bytes, bytes]:
    """Return the packaged Linux executable and the libduckdb beside it."""
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = [name for name in archive.namelist() if name.endswith(_WHEEL_SUFFIX)]
            if len(names) != 1:
                raise ArtifactError.wheel_entries(wheel, names)
            library = names[0].rsplit("/", 1)[0] + "/" + _LIBRARY
            if library not in archive.namelist():
                raise ArtifactError.missing_library(wheel)
            return archive.read(names[0]), archive.read(library)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ArtifactError.wheel_read(wheel, exc) from exc


def npm_payload(tarball: Path) -> tuple[bytes, bytes]:
    """Return bin/okf-core and bin/libduckdb.so bytes from one npm-native tarball."""
    try:
        with tarfile.open(tarball, mode="r:gz") as archive:
            payloads = []
            for name in (_NPM_MEMBER, _NPM_MEMBER.rsplit("/", 1)[0] + "/" + _LIBRARY):
                file_object = archive.extractfile(archive.getmember(name))
                if file_object is None:
                    raise ArtifactError.npm_member(tarball)
                payloads.append(file_object.read())
            return payloads[0], payloads[1]
    except (OSError, KeyError, tarfile.TarError) as exc:
        raise ArtifactError.npm_read(tarball, exc) from exc


def digest(data: bytes) -> str:
    """Return the SHA-256 identity used to prove byte reuse."""
    return hashlib.sha256(data).hexdigest()


def extract(wheel: Path, destination: Path) -> str:
    """Write the wheel executable and its libduckdb verbatim into the npm staging dir."""
    executable, library = wheel_payload(wheel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    for path, payload in ((destination, executable), (destination.parent / _LIBRARY, library)):
        path.write_bytes(payload)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return digest(executable)


def verify(wheel: Path, tarball: Path) -> str:
    """Prove the npm-native executable and library are byte-identical to the wheel's."""
    wheel_files = wheel_payload(wheel)
    npm_files = npm_payload(tarball)
    for wheel_bytes, npm_bytes in zip(wheel_files, npm_files, strict=True):
        if wheel_bytes != npm_bytes:
            raise ArtifactError.mismatch(digest(wheel_bytes), digest(npm_bytes))
    return digest(wheel_files[0])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    extract_command = commands.add_parser("extract")
    extract_command.add_argument("wheel", type=Path)
    extract_command.add_argument("destination", type=Path)
    verify_command = commands.add_parser("verify")
    verify_command.add_argument("wheel", type=Path)
    verify_command.add_argument("tarball", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run extract or byte-identity verification."""
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "extract":
            sha256 = extract(arguments.wheel, arguments.destination)
        else:
            sha256 = verify(arguments.wheel, arguments.tarball)
    except ArtifactError as exc:
        sys.stderr.write(f"native artifact error: {exc}\n")
        return 1
    sys.stdout.write(f"native executable sha256={sha256}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
