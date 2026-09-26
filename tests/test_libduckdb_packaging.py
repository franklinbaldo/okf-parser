"""Tests for linking and shipping the prebuilt libduckdb in wheels."""

from __future__ import annotations

import base64
import hashlib
import io
import struct
import zipfile
from typing import TYPE_CHECKING

import pytest

from scripts import fetch_libduckdb
from scripts.check_duckdb_shipped import check
from scripts.fetch_libduckdb import Archive, FetchError, crate_duckdb_version, fetch, thin
from scripts.ship_libduckdb import ShipError, ship

if TYPE_CHECKING:
    from pathlib import Path

SCRIPTS = "okf_parser-1.2.3.data/scripts"
RECORD = "okf_parser-1.2.3.dist-info/RECORD"


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _wheel(path: Path, files: dict[str, bytes]) -> Path:
    path.write_bytes(_zip(files))
    return path


def test_the_pins_follow_the_locked_crate(tmp_path: Path) -> None:
    lockfile = tmp_path / "Cargo.lock"
    lockfile.write_text('[[package]]\nname = "duckdb"\nversion = "1.10504.0"\n', encoding="utf-8")
    assert crate_duckdb_version(lockfile) == "1.5.4"
    lockfile.write_text('[[package]]\nname = "duckdb"\nversion = "1.20001.3"\n', encoding="utf-8")
    assert crate_duckdb_version(lockfile) == "2.0.1"


def test_a_universal_library_is_cut_to_one_slice() -> None:
    arm, intel = b"\xcf\xfa\xed\xfearm-slice", b"\xcf\xfa\xed\xfeintel-slice"
    header = struct.pack(">II", 0xCAFEBABE, 2)
    offset = 8 + 2 * 20
    header += struct.pack(">IIIII", 0x01000007, 3, offset, len(intel), 0)
    header += struct.pack(">IIIII", 0x0100000C, 0, offset + len(intel), len(arm), 0)
    fat = header + intel + arm
    assert thin(fat, 0x0100000C) == arm
    assert thin(fat, 0x01000007) == intel
    assert thin(arm, 0x0100000C) == arm
    with pytest.raises(FetchError, match="no slice"):
        thin(fat, 0x12)


def test_fetch_verifies_the_pin_and_extracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _zip({"libduckdb.so": b"lib", "duckdb.h": b"header", "duckdb.hpp": b"big"})
    pinned = Archive("libduckdb-test.zip", hashlib.sha256(payload).hexdigest(), "libduckdb.so")
    monkeypatch.setitem(fetch_libduckdb.ARCHIVES, "test-target", pinned)

    library = fetch("test-target", tmp_path / "out", payload)

    assert library.read_bytes() == b"lib"
    assert (tmp_path / "out" / "duckdb.h").read_bytes() == b"header"
    assert not (tmp_path / "out" / "duckdb.hpp").exists()
    with pytest.raises(FetchError, match="does not match the pin"):
        fetch("test-target", tmp_path / "again", payload + b"tampered")
    with pytest.raises(FetchError, match="no pinned libduckdb"):
        fetch("sparc-sun-solaris", tmp_path / "none", payload)


def test_ship_puts_the_library_beside_the_executable_and_records_it(tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path / "okf_parser.whl",
        {f"{SCRIPTS}/okf-parser": b"binary libduckdb.so", RECORD: f"{RECORD},,\n".encode()},
    )
    library = tmp_path / "libduckdb.so"
    library.write_bytes(b"the library")

    member = ship(wheel, library)

    assert member == f"{SCRIPTS}/libduckdb.so"
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read(member) == b"the library"
        assert archive.getinfo(member).external_attr >> 16 == 0o100755
        record = archive.read(RECORD).decode()
    digest = base64.urlsafe_b64encode(hashlib.sha256(b"the library").digest()).rstrip(b"=")
    assert f"{member},sha256={digest.decode()},11" in record
    assert record.rstrip().endswith(f"{RECORD},,")
    assert check(wheel) == []
    with pytest.raises(ShipError, match="already ships"):
        ship(wheel, library)


def test_check_requires_the_linked_library_and_nothing_else(tmp_path: Path) -> None:
    missing = _wheel(tmp_path / "missing.whl", {f"{SCRIPTS}/okf-parser.exe": b"imports duckdb.dll"})
    assert check(missing) == [
        f"missing.whl: links duckdb.dll but does not ship {SCRIPTS}/duckdb.dll"
    ]
    static = _wheel(tmp_path / "static.whl", {f"{SCRIPTS}/okf-parser": b"no dynamic duckdb"})
    assert "should link one libduckdb" in check(static)[0]
    stray = _wheel(
        tmp_path / "stray.whl",
        {
            f"{SCRIPTS}/okf-parser": b"needs libduckdb.dylib",
            f"{SCRIPTS}/libduckdb.dylib": b"lib",
            "okf_parser.libs/libduckdb-1a2b.dylib": b"lib",
        },
    )
    assert check(stray) == [
        "stray.whl: ships an unexpected DuckDB library at okf_parser.libs/libduckdb-1a2b.dylib"
    ]
