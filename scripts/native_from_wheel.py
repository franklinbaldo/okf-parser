#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Reuse the exact Linux x86_64 executable from a Python wheel in npm-native."""

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
    def mismatch(cls, wheel_sha256: str, npm_sha256: str) -> ArtifactError:
        """Build an error when wheel and npm executables are not identical."""
        return cls(f"native executable mismatch: wheel={wheel_sha256} npm={npm_sha256}")


def wheel_executable(wheel: Path) -> bytes:
    """Return the single packaged Linux executable from a maturin bin wheel."""
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = [name for name in archive.namelist() if name.endswith(_WHEEL_SUFFIX)]
            if len(names) != 1:
                raise ArtifactError.wheel_entries(wheel, names)
            return archive.read(names[0])
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ArtifactError.wheel_read(wheel, exc) from exc


def npm_executable(tarball: Path) -> bytes:
    """Return bin/okf-core bytes from one npm-native tarball."""
    try:
        with tarfile.open(tarball, mode="r:gz") as archive:
            member = archive.getmember(_NPM_MEMBER)
            file_object = archive.extractfile(member)
            if file_object is None:
                raise ArtifactError.npm_member(tarball)
            return file_object.read()
    except (OSError, KeyError, tarfile.TarError) as exc:
        raise ArtifactError.npm_read(tarball, exc) from exc


def digest(data: bytes) -> str:
    """Return the SHA-256 identity used to prove byte reuse."""
    return hashlib.sha256(data).hexdigest()


def extract(wheel: Path, destination: Path) -> str:
    """Write the wheel executable verbatim to the npm-native staging path."""
    payload = wheel_executable(wheel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return digest(payload)


def verify(wheel: Path, tarball: Path) -> str:
    """Prove the npm-native executable is byte-identical to the wheel executable."""
    wheel_payload = wheel_executable(wheel)
    npm_payload = npm_executable(tarball)
    if wheel_payload != npm_payload:
        raise ArtifactError.mismatch(digest(wheel_payload), digest(npm_payload))
    return digest(wheel_payload)


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
