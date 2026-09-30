#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Derive release metadata from Cargo.toml without resolving dependencies.

Only the workspace package version is authored. The remaining numbers are
package-manager inputs or generated output. Preserve registry resolutions and
integrities: an unpublished release must be synchronizable entirely offline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

if __package__:
    from scripts.project_version import project_version
    from scripts.pypi_readme import render
else:
    from project_version import project_version
    from pypi_readme import render

if TYPE_CHECKING:
    from collections.abc import Mapping

PARSER = "@franklinbaldo/okf-parser"
NATIVE = "@franklinbaldo/okf-parser-native-linux-x64"
CRATES = ("okf-core", "okf-db", "okf-engine")
ACTION = re.compile(r"(franklinbaldo/okf-parser@v)[^\s`\"'<>()]+")


def _replace(text: str, pattern: str, replacement: str, *, count: int = 1) -> str:
    result, found = re.subn(pattern, lambda match: match[1] + replacement, text)
    if found != count:
        message = f"expected {count} occurrence(s) of {pattern!r}, found {found}"
        raise ValueError(message)
    return result


def _json_field(text: str, key: str, old: str, new: str) -> str:
    """Replace a known scalar without reformatting an authored manifest."""
    pattern = rf"({re.escape(json.dumps(key))}\s*:\s*){re.escape(json.dumps(old))}"
    return _replace(text, pattern, json.dumps(new))


def _manifest(text: str, name: str, version: str, pins: Mapping[str, str]) -> str:
    data = json.loads(text)
    if data["name"] != name:
        message = f"expected npm package {name!r}, found {data['name']!r}"
        raise ValueError(message)
    text = _json_field(text, "version", data["version"], version)
    for section, dependency in pins.items():
        old = data[section][dependency]
        new = f"^{version}" if section == "peerDependencies" else version
        text = _json_field(text, dependency, old, new)
    return text


def _npm_lock(text: str, version: str, local: str) -> str:
    data = json.loads(text)
    data["version"] = version
    # These are local workspace snapshots, never registry tarballs. Keep all
    # node_modules entries, resolved URLs, integrity hashes and links intact.
    snapshots = (
        (("", PARSER), (local, NATIVE))
        if local == "../native-npm-linux-x64"
        else (("", f"{PARSER}-duckdb"), (local, PARSER))
    )
    for key, name in snapshots:
        package = data["packages"][key]
        if package.get("name") != name:
            message = f"expected local npm lock snapshot {key!r} to name {name!r}"
            raise ValueError(message)
        package["version"] = version
        if name == PARSER:
            package.setdefault("optionalDependencies", {})[NATIVE] = version
        elif name == f"{PARSER}-duckdb":
            package.setdefault("peerDependencies", {})[PARSER] = f"^{version}"
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _cargo_lock(text: str, version: str, names: tuple[str, ...]) -> str:
    sections = text.split("[[package]]")
    found = set()
    for index, section in enumerate(sections[1:], 1):
        package = tomllib.loads(section)
        if package.get("name") in names and "source" not in package:
            found.add(package["name"])
            sections[index] = _replace(section, r'(?m)^(version = )"[^"]+"$', f'"{version}"')
    if found != set(names):
        message = f"missing local Cargo packages: {set(names) - found}"
        raise ValueError(message)
    return "[[package]]".join(sections)


def derived_files(root: Path) -> dict[Path, str]:
    """Compute every derivative before writing, so malformed inputs fail early."""
    version = project_version(root / "Cargo.toml")
    outputs: dict[Path, str] = {}

    def read(relative: str) -> str:
        return (root / relative).read_text(encoding="utf-8")

    for directory, dependencies in (
        ("okf-db", ("okf-engine",)),
        ("rust-core", ("okf-db", "okf-engine")),
    ):
        relative = f"{directory}/Cargo.toml"
        text = read(relative)
        for dependency in dependencies:
            pattern = rf'(?m)^({dependency} = \{{[^\n]*?version = )"[^"]+"'
            text = _replace(text, pattern, f'"{version}"')
        outputs[root / relative] = text

    for directory, name, pins in (
        ("typescript", PARSER, {"optionalDependencies": NATIVE}),
        ("typescript-duckdb", f"{PARSER}-duckdb", {"peerDependencies": PARSER}),
        ("native-npm-linux-x64", NATIVE, {}),
    ):
        relative = f"{directory}/package.json"
        outputs[root / relative] = _manifest(read(relative), name, version, pins)

    for directory, local in (
        ("typescript", "../native-npm-linux-x64"),
        ("typescript-duckdb", "../typescript"),
    ):
        relative = f"{directory}/package-lock.json"
        outputs[root / relative] = _npm_lock(read(relative), version, local)

    for relative, names in (
        ("Cargo.lock", CRATES),
        ("duckdb-extension/Cargo.lock", ("okf-engine",)),
    ):
        outputs[root / relative] = _cargo_lock(read(relative), version, names)

    # uv omits the version of an editable project with dynamic metadata.
    # All dependency resolutions stay unchanged; uv lock --check validates it.
    outputs[root / "uv.lock"] = re.sub(
        r'(\[\[package\]\]\nname = "okf-parser"\n)version = "[^"\n]+"\n',
        r"\1",
        read("uv.lock"),
    )
    outputs[root / "typescript/src/version.ts"] = (
        "// Generated by scripts/sync_versions.py from Cargo.toml; do not edit.\n"
        f'export const PROTOCOL_VERSION = "{version}";\n'
    )
    readme, count = ACTION.subn(rf"\g<1>{version}", read("README.md"))
    if count != 1:
        message = f"expected one README Action version, found {count}"
        raise ValueError(message)
    outputs[root / "README.md"] = readme
    outputs[root / "README.pypi.md"] = render(readme)
    return outputs


def main(argv: list[str] | None = None) -> int:
    """Synchronize derived metadata, or check it without changing files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="report stale files without writing")
    args = parser.parse_args(argv)
    try:
        outputs = derived_files(args.root)
        stale = [
            path
            for path, text in outputs.items()
            if not path.exists() or path.read_text(encoding="utf-8") != text
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.stderr.write(f"cannot synchronize versions: {exc}\n")
        return 1
    if args.check:
        for path in stale:
            sys.stderr.write(
                f"{path.relative_to(args.root)} is stale; run scripts/sync_versions.py\n"
            )
        return int(bool(stale))
    for path in stale:
        path.write_text(outputs[path], encoding="utf-8", newline="\n")
        sys.stdout.write(f"updated {path.relative_to(args.root)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
