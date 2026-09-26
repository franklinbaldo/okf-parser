"""Tests for byte-identical reuse of the wheel executable in npm-native."""

from __future__ import annotations

import io
import tarfile
import zipfile
from typing import TYPE_CHECKING

import pytest

from scripts.native_from_wheel import ArtifactError, extract, verify

if TYPE_CHECKING:
    from pathlib import Path


LIBRARY = b"\x7fELFlibduckdb"


def _wheel(path: Path, payload: bytes, library: bytes | None = LIBRARY) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr("okf_parser-1.2.3.data/scripts/okf-parser", payload)
        if library is not None:
            archive.writestr("okf_parser-1.2.3.data/scripts/libduckdb.so", library)


def _npm(path: Path, payload: bytes, library: bytes = LIBRARY) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        members = (("package/bin/okf-core", payload), ("package/bin/libduckdb.so", library))
        for name, data in members:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def test_extract_and_verify_reuse_exact_bytes(tmp_path: Path) -> None:
    wheel = tmp_path / "parser.whl"
    destination = tmp_path / "package" / "bin" / "okf-core"
    tarball = tmp_path / "native.tgz"
    payload = b"\x7fELFsame-native-engine"
    _wheel(wheel, payload)

    sha256 = extract(wheel, destination)
    assert destination.read_bytes() == payload
    assert (destination.parent / "libduckdb.so").read_bytes() == LIBRARY
    assert sha256

    _npm(tarball, destination.read_bytes())
    assert verify(wheel, tarball) == sha256


def test_verify_rejects_a_rebuilt_or_changed_native_binary(tmp_path: Path) -> None:
    wheel = tmp_path / "parser.whl"
    tarball = tmp_path / "native.tgz"
    _wheel(wheel, b"wheel-binary")
    _npm(tarball, b"different-build")

    with pytest.raises(ArtifactError, match="native executable mismatch"):
        verify(wheel, tarball)


def test_verify_rejects_a_different_library(tmp_path: Path) -> None:
    wheel = tmp_path / "parser.whl"
    tarball = tmp_path / "native.tgz"
    _wheel(wheel, b"same-binary")
    _npm(tarball, b"same-binary", library=b"another-duckdb")

    with pytest.raises(ArtifactError, match="native executable mismatch"):
        verify(wheel, tarball)


def test_a_wheel_without_its_library_is_refused(tmp_path: Path) -> None:
    wheel = tmp_path / "parser.whl"
    _wheel(wheel, b"binary", library=None)

    with pytest.raises(ArtifactError, match=r"does not ship libduckdb\.so"):
        extract(wheel, tmp_path / "bin" / "okf-core")
