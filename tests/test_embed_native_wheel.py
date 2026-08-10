"""Native wheel repackaging tests."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from scripts.embed_native_wheel import embed_native_wheel


def _portable_wheel(tmp_path: Path) -> Path:
    wheel = tmp_path / "okf_parser-0.40.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("okf_parser/__init__.py", b"")
        archive.writestr(
            "okf_parser-0.40.0.dist-info/WHEEL",
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr("okf_parser-0.40.0.dist-info/METADATA", b"Name: okf-parser\nVersion: 0.40.0\n")
        archive.writestr("okf_parser-0.40.0.dist-info/RECORD", b"")
    return wheel


def test_embed_native_wheel_retags_and_records_executable(tmp_path: Path) -> None:
    wheel = _portable_wheel(tmp_path)
    binary = tmp_path / "okf-core"
    binary.write_bytes(b"native")
    result = embed_native_wheel(wheel, binary, platform_tag="linux_x86_64")

    assert result.name.endswith("-py3-none-linux_x86_64.whl")
    with zipfile.ZipFile(result) as archive:
        assert archive.read("okf_parser/_native/okf-core") == b"native"
        wheel_metadata = archive.read("okf_parser-0.40.0.dist-info/WHEEL").decode()
        assert "Root-Is-Purelib: false" in wheel_metadata
        assert "Tag: py3-none-linux_x86_64" in wheel_metadata
        rows = list(
            csv.reader(
                io.StringIO(archive.read("okf_parser-0.40.0.dist-info/RECORD").decode())
            )
        )
        assert any(row[0] == "okf_parser/_native/okf-core" and row[1] for row in rows)
        mode = archive.getinfo("okf_parser/_native/okf-core").external_attr >> 16
        assert mode & 0o111
