"""Inject a prebuilt libduckdb dynamic library into a built wheel.

`DUCKDB_DOWNLOAD_LIB=1` (see issue #154) makes `libduckdb-sys` download a
prebuilt dynamic library instead of compiling DuckDB from source. maturin's
`bindings = "bin"` packaging only knows about the `okf-parser` executable
itself, so the downloaded library and the executable's runtime search path
both need to be fixed up by hand after `maturin build` produces the wheel:

- the dynamic library is added to the wheel next to the executable, inside
  `<pkg>.data/scripts/`;
- the executable's rpath (Linux `patchelf`, macOS `install_name_tool`) is
  rewritten to look next to itself (`$ORIGIN` / `@executable_path`), because
  the rpath `libduckdb-sys`'s build script embeds at compile time points at
  the build machine's download cache, which does not exist on an end user's
  machine. Windows needs no rpath fix: a DLL next to the `.exe` is found
  automatically.

The wheel's `RECORD` file is updated to list the new library file with its
hash and size, per the wheel spec (PEP 427) -- every installed file must be
recorded so `pip`/`uv` can verify and later uninstall it correctly.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

_SCRIPT_SUFFIXES = (".data/scripts/okf-parser", ".data/scripts/okf-parser.exe")


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _find_record_entry(names: list[str]) -> str:
    for name in names:
        if name.endswith(".dist-info/RECORD"):
            return name
    message = "wheel has no *.dist-info/RECORD entry"
    raise SystemExit(message)


def _find_script_entry(names: list[str]) -> str:
    matches = [name for name in names if name.endswith(_SCRIPT_SUFFIXES)]
    if len(matches) != 1:
        message = f"expected exactly one okf-parser script entry, found {matches}"
        raise SystemExit(message)
    return matches[0]


def _patch_rpath(binary_path: Path, platform: str) -> None:
    """Rewrite the executable's rpath to look next to itself, not the build cache."""
    if platform == "linux":
        subprocess.run(  # noqa: S603 -- fixed argv, trusted local tool, no untrusted input
            ["patchelf", "--set-rpath", "$ORIGIN", str(binary_path)],  # noqa: S607
            check=True,
        )
    elif platform == "macos":
        subprocess.run(  # noqa: S603 -- fixed argv, trusted local tool, no untrusted input
            ["install_name_tool", "-add_rpath", "@executable_path", str(binary_path)],  # noqa: S607
            check=True,
        )
    elif platform == "windows":
        pass
    else:
        message = f"unknown platform {platform!r}"
        raise SystemExit(message)


class _TempCopy:
    """Write bytes to a temp file, yield its path, clean up on exit."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._dir: str | None = None

    def __enter__(self) -> Path:
        self._dir = tempfile.mkdtemp(prefix="bundle-duckdb-")
        path = Path(self._dir) / "okf-parser-binary"
        path.write_bytes(self._data)
        path.chmod(0o755)
        return path

    def __exit__(self, *exc_info: object) -> None:
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)


def _rewrite_record(
    entries: dict[str, tuple[zipfile.ZipInfo, bytes]],
    record_entry: str,
    changed: set[str],
) -> None:
    record_info, record_bytes = entries[record_entry]
    rows = list(csv.reader(io.StringIO(record_bytes.decode("utf-8"))))
    kept = [row for row in rows if row and row[0] not in changed and row[0] != record_entry]
    for name in changed:
        _info, data = entries[name]
        kept.append([name, _record_hash(data), str(len(data))])
    kept.append([record_entry, "", ""])

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(kept)
    entries[record_entry] = (record_info, buffer.getvalue().encode("utf-8"))


def bundle(wheel_path: Path, lib_path: Path, platform: str) -> None:
    """Add `lib_path` next to the packaged executable and fix its rpath."""
    with zipfile.ZipFile(wheel_path) as source:
        names = source.namelist()
        script_entry = _find_script_entry(names)
        record_entry = _find_record_entry(names)
        entries = {name: (source.getinfo(name), source.read(name)) for name in names}

    scripts_dir = script_entry.rsplit("/", 1)[0]
    lib_entry = f"{scripts_dir}/{lib_path.name}"

    script_info, script_bytes = entries[script_entry]
    with _TempCopy(script_bytes) as tmp_binary:
        _patch_rpath(tmp_binary, platform)
        patched_script_bytes = tmp_binary.read_bytes()
    entries[script_entry] = (script_info, patched_script_bytes)

    lib_bytes = lib_path.read_bytes()
    lib_info = zipfile.ZipInfo(lib_entry, date_time=script_info.date_time)
    lib_info.external_attr = script_info.external_attr
    lib_info.compress_type = script_info.compress_type
    entries[lib_entry] = (lib_info, lib_bytes)

    _rewrite_record(entries, record_entry, changed={script_entry, lib_entry})

    tmp_wheel = wheel_path.with_suffix(".whl.tmp")
    with zipfile.ZipFile(tmp_wheel, "w", zipfile.ZIP_DEFLATED) as out:
        for info, data in entries.values():
            out.writestr(info, data)
    tmp_wheel.replace(wheel_path)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: bundle a downloaded libduckdb into a built wheel."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--lib", required=True, type=Path)
    parser.add_argument("--platform", required=True, choices=["linux", "macos", "windows"])
    args = parser.parse_args(argv)

    if not args.wheel.is_file():
        message = f"wheel not found: {args.wheel}"
        raise SystemExit(message)
    if not args.lib.is_file():
        message = f"library not found: {args.lib}"
        raise SystemExit(message)

    bundle(args.wheel, args.lib, args.platform)
    sys.stdout.write(f"bundled {args.lib.name} into {args.wheel.name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
