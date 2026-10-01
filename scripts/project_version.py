#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Read the release version from the root Cargo workspace manifest.

The workspace owns the version shared by Rust, Python, npm and DuckDB artifacts.
The normal reader never falls back to a generated or legacy manifest. The
``--ref`` mode also understands the old pyproject.toml declaration so CI can
compare a migrated branch with a base that predates workspace versioning.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

_STABLE_SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


class VersionError(ValueError):
    """Report a missing or malformed project version."""


class _MissingVersionError(VersionError):
    """Identify a historical manifest that predates workspace versioning."""


def _declared_version(contents: str, source: str, *, legacy: bool = False) -> str:
    try:
        data = tomllib.loads(contents)
    except tomllib.TOMLDecodeError as exc:
        message = f"cannot read {source}: {exc}"
        raise VersionError(message) from exc
    keys = ("project", "version") if legacy else ("workspace", "package", "version")
    value: object = data
    for key in keys:
        if not isinstance(value, dict):
            message = f"{source} has a malformed {'.'.join(keys)} declaration"
            raise VersionError(message)
        if key not in value:
            message = f"{source} has no {'.'.join(keys)}"
            raise _MissingVersionError(message)
        value = value[key]
    if not isinstance(value, str) or not _STABLE_SEMVER.fullmatch(value):
        message = f"{source}: {'.'.join(keys)} must be a stable SemVer (X.Y.Z), got {value!r}"
        raise VersionError(message)
    return value


def project_version(cargo_manifest: Path) -> str:
    """Return the stable `workspace.package.version` from a root Cargo.toml."""
    try:
        contents = cargo_manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        message = f"cannot read {cargo_manifest}: {exc}"
        raise VersionError(message) from exc
    return _declared_version(contents, str(cargo_manifest))


def _git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - fixed git executable, no shell
            ["git", "-C", str(directory), *arguments],  # noqa: S607
            capture_output=True,
            check=False,
            encoding="utf-8",
        )
    except (OSError, UnicodeError) as exc:
        message = f"cannot read Git history in {directory}: {exc}"
        raise VersionError(message) from exc


def _version_at_ref(cargo_manifest: Path, ref: str) -> str:
    manifest = cargo_manifest.resolve()
    location = _git(manifest.parent, "rev-parse", "--show-toplevel")
    if location.returncode:
        message = f"cannot find Git repository for {manifest}: {location.stderr.strip()}"
        raise VersionError(message)
    repository = Path(location.stdout.strip())
    revision = _git(repository, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    if revision.returncode:
        message = f"cannot resolve Git ref {ref!r}: {revision.stderr.strip()}"
        raise VersionError(message)
    commit = revision.stdout.strip()
    relative_manifest = manifest.relative_to(repository)
    object_name = f"{commit}:{relative_manifest.as_posix()}"
    historical = _git(repository, "show", object_name)
    if not historical.returncode:
        try:
            return _declared_version(historical.stdout, f"{ref}:{relative_manifest.as_posix()}")
        except _MissingVersionError:
            pass

    # Only a historical comparison may consult the old Python declaration.
    # Invalid TOML or a declared-but-invalid workspace version fails above.
    legacy_path = relative_manifest.with_name("pyproject.toml").as_posix()
    legacy = _git(repository, "show", f"{commit}:{legacy_path}")
    if legacy.returncode:
        message = f"{ref} has neither workspace.package.version nor a legacy {legacy_path}"
        raise VersionError(message)
    return _declared_version(legacy.stdout, f"{ref}:{legacy_path}", legacy=True)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: print the project version on one line."""
    parser = argparse.ArgumentParser(description="Print workspace.package.version from Cargo.toml.")
    parser.add_argument("--manifest", type=Path, default=Path("Cargo.toml"))
    parser.add_argument(
        "--ref",
        help="Read a base Git ref, allowing legacy pyproject.toml for pre-migration comparisons.",
    )
    args = parser.parse_args(argv)

    try:
        version = (
            _version_at_ref(args.manifest, args.ref)
            if args.ref is not None
            else project_version(args.manifest)
        )
    except VersionError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"{version}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
