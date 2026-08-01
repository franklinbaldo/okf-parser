from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.release_contract import ContractError, build_manifest, verify_local, verify_source

VERSION = "1.2.3"
COMMIT = "a" * 40


def _write_source(root: Path, *, protocol_version: str = VERSION) -> None:
    (root / "typescript" / "src").mkdir(parents=True)
    (root / "typescript-duckdb").mkdir()
    (root / "changelog").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "okf-parser"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (root / "typescript" / "package.json").write_text(
        json.dumps({"name": "okf-parser", "version": VERSION}), encoding="utf-8"
    )
    (root / "typescript-duckdb" / "package.json").write_text(
        json.dumps(
            {
                "name": "okf-parser-duckdb",
                "version": VERSION,
                "peerDependencies": {"okf-parser": f"^{VERSION}"},
            }
        ),
        encoding="utf-8",
    )
    (root / "typescript" / "src" / "version.ts").write_text(
        f'export const PROTOCOL_VERSION = "{protocol_version}";\n', encoding="utf-8"
    )
    (root / "changelog" / f"{VERSION}.md").write_text(
        f"---\ntype: Release\ntitle: okf-parser {VERSION}\ndescription: Test\n---\n",
        encoding="utf-8",
    )


def _tar_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(data)
    archive.addfile(member, io.BytesIO(data))


def _write_artifacts(release: Path) -> None:
    python_dir = release / "python"
    npm_dir = release / "npm"
    python_dir.mkdir(parents=True)
    npm_dir.mkdir()
    wheel = python_dir / f"okf_parser-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr(
            f"okf_parser-{VERSION}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: okf-parser\nVersion: {VERSION}\n",
        )
    with tarfile.open(python_dir / f"okf_parser-{VERSION}.tar.gz", mode="w:gz") as archive:
        _tar_member(
            archive,
            f"okf_parser-{VERSION}/PKG-INFO",
            f"Metadata-Version: 2.4\nName: okf-parser\nVersion: {VERSION}\n".encode(),
        )
    for package in ("okf-parser", "okf-parser-duckdb"):
        with tarfile.open(npm_dir / f"{package}-{VERSION}.tgz", mode="w:gz") as archive:
            _tar_member(
                archive,
                "package/package.json",
                json.dumps({"name": package, "version": VERSION}).encode(),
            )


def _build(root: Path) -> dict[str, object]:
    release = root / "release"
    _write_artifacts(release)
    return build_manifest(
        root,
        release,
        "example/okf-parser",
        COMMIT,
        f"v{VERSION}",
        {"python": "3.12", "node": "24", "npm": "11", "uv": "0.11"},
    )


def test_verify_source_accepts_synchronized_metadata(tmp_path: Path) -> None:
    _write_source(tmp_path)
    contract = verify_source(tmp_path, f"v{VERSION}")
    assert contract.version == VERSION


def test_verify_source_rejects_protocol_drift(tmp_path: Path) -> None:
    _write_source(tmp_path, protocol_version="1.2.2")
    with pytest.raises(ContractError, match="protocol version"):
        verify_source(tmp_path)


def test_verify_source_rejects_wrong_tag(tmp_path: Path) -> None:
    _write_source(tmp_path)
    with pytest.raises(ContractError, match="tag must be"):
        verify_source(tmp_path, "v1.2.2")


def test_manifest_records_and_reverifies_exact_artifacts(tmp_path: Path) -> None:
    _write_source(tmp_path)
    manifest = _build(tmp_path)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    assert len(artifacts) == 4
    assert verify_local(tmp_path, tmp_path / "release" / "manifest.json") == manifest


def test_verify_local_rejects_tampering(tmp_path: Path) -> None:
    _write_source(tmp_path)
    manifest = _build(tmp_path)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    first = artifacts[0]
    assert isinstance(first, dict)
    target = tmp_path / "release" / str(first["path"])
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(ContractError, match="size mismatch"):
        verify_local(tmp_path, tmp_path / "release" / "manifest.json")


def test_build_manifest_rejects_unexpected_artifact(tmp_path: Path) -> None:
    _write_source(tmp_path)
    release = tmp_path / "release"
    _write_artifacts(release)
    (release / "npm" / "extra.tgz").write_bytes(b"extra")
    with pytest.raises(ContractError, match="unexpected release artifacts"):
        build_manifest(tmp_path, release, "example/okf-parser", COMMIT, "main", {})


def test_verify_local_rejects_path_traversal(tmp_path: Path) -> None:
    _write_source(tmp_path)
    _build(tmp_path)
    path = tmp_path / "release" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["path"] = "../escape.whl"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ContractError, match="unsafe"):
        verify_local(tmp_path, path)
