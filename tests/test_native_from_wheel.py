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


def _wheel(path: Path, payload: bytes) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        archive.writestr("okf_parser-1.2.3.data/scripts/okf-parser", payload)


def _npm(path: Path, payload: bytes) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        member = tarfile.TarInfo("package/bin/okf-core")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))


def test_extract_and_verify_reuse_exact_bytes(tmp_path: Path) -> None:
    wheel = tmp_path / "parser.whl"
    destination = tmp_path / "package" / "bin" / "okf-core"
    tarball = tmp_path / "native.tgz"
    payload = b"\x7fELFsame-native-engine"
    _wheel(wheel, payload)

    sha256 = extract(wheel, destination)
    assert destination.read_bytes() == payload
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
