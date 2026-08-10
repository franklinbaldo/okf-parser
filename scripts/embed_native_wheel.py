#!/usr/bin/env python3
"""Turn the portable okf-parser wheel into one platform wheel with okf-core embedded."""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import zipfile
from pathlib import Path


def _record_digest(data: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def _wheel_name(source: Path, platform_tag: str) -> Path:
    suffix = "-py3-none-any.whl"
    if not source.name.endswith(suffix):
        raise ValueError(f"expected a py3-none-any wheel, found {source.name}")
    return source.with_name(source.name.removesuffix(suffix) + f"-py3-none-{platform_tag}.whl")


def embed_native_wheel(
    wheel: Path,
    binary: Path,
    *,
    platform_tag: str,
    replace: bool = False,
) -> Path:
    """Embed one executable and rewrite wheel metadata/RECORD deterministically."""
    output = _wheel_name(wheel, platform_tag)
    binary_bytes = binary.read_bytes()
    members: dict[str, tuple[bytes, int]] = {}

    with zipfile.ZipFile(wheel) as source:
        dist_info: str | None = None
        for info in source.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name.endswith(".dist-info/WHEEL"):
                dist_info = name.removesuffix("WHEEL")
            if name.endswith(".dist-info/RECORD"):
                continue
            data = source.read(info)
            mode = (info.external_attr >> 16) & 0o777
            members[name] = (data, mode or 0o644)

    if dist_info is None:
        raise ValueError(f"wheel has no .dist-info/WHEEL: {wheel}")

    wheel_metadata_name = f"{dist_info}WHEEL"
    metadata = members[wheel_metadata_name][0].decode("utf-8")
    metadata = re.sub(r"^Root-Is-Purelib: true$", "Root-Is-Purelib: false", metadata, flags=re.M)
    metadata = re.sub(r"^Tag: py3-none-any$", f"Tag: py3-none-{platform_tag}", metadata, flags=re.M)
    members[wheel_metadata_name] = (metadata.encode("utf-8"), 0o644)
    members["okf_parser/_native/okf-core"] = (binary_bytes, 0o755)

    record_name = f"{dist_info}RECORD"
    record_lines = [
        f"{name},{_record_digest(data)},{len(data)}"
        for name, (data, _mode) in sorted(members.items())
    ]
    record_lines.append(f"{record_name},,")
    members[record_name] = (("\n".join(record_lines) + "\n").encode("utf-8"), 0o644)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, (data, mode) in sorted(members.items()):
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = mode << 16
            target.writestr(info, data)

    if replace:
        wheel.unlink()
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--platform-tag", required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    output = embed_native_wheel(
        args.wheel,
        args.binary,
        platform_tag=args.platform_tag,
        replace=args.replace,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
