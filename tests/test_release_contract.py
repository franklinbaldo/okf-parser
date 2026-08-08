"""Tests for synchronized release metadata and artifact verification."""

from __future__ import annotations

import io
import json
import re
import tarfile
import zipfile
from typing import TYPE_CHECKING, cast

import pytest

from scripts.release_contract import (
    BuildContext,
    ContractError,
    build_manifest,
    verify_contents,
    verify_local,
    verify_source,
)

if TYPE_CHECKING:
    from pathlib import Path

VERSION = "1.2.3"
COMMIT = "a" * 40
BUILD_CONTEXT = BuildContext(
    repository="example/okf-parser",
    commit=COMMIT,
    ref=f"v{VERSION}",
    tools={"python": "3.12", "node": "24", "npm": "11", "uv": "0.11"},
)


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
    (root / "README.md").write_text(
        f"- uses: franklinbaldo/okf-parser@v{VERSION}\n", encoding="utf-8"
    )


def _tar_bytes(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    file_object = archive.extractfile(member)
    assert file_object is not None
    return file_object.read()


def _tar_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(data)
    archive.addfile(member, io.BytesIO(data))


WHEEL_MEMBERS = ("okf_parser/__init__.py", "okf_parser/cli.py", "okf_parser/parser.py")
SDIST_MEMBERS = ("README.md", "pyproject.toml", "src/okf_parser/__init__.py")
NPM_MEMBERS = (
    "README.md",
    "LICENSE",
    "dist/index.js",
    "dist/index.d.ts",
    "dist/cli.js",
    "dist/mcp.js",
)


def _write_artifacts(release: Path, extra: dict[str, tuple[str, ...]] | None = None) -> None:
    additions = extra or {}
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
        for member in WHEEL_MEMBERS + additions.get("python-wheel", ()):
            archive.writestr(member, "content\n")
    root = f"okf_parser-{VERSION}"
    with tarfile.open(python_dir / f"okf_parser-{VERSION}.tar.gz", mode="w:gz") as archive:
        _tar_member(
            archive,
            f"{root}/PKG-INFO",
            f"Metadata-Version: 2.4\nName: okf-parser\nVersion: {VERSION}\n".encode(),
        )
        for member in SDIST_MEMBERS + additions.get("python-sdist", ()):
            _tar_member(archive, f"{root}/{member}", b"content\n")
    kinds = {"okf-parser": "npm-parser", "okf-parser-duckdb": "npm-duckdb"}
    for package, kind in kinds.items():
        with tarfile.open(npm_dir / f"{package}-{VERSION}.tgz", mode="w:gz") as archive:
            _tar_member(
                archive,
                "package/package.json",
                json.dumps({"name": package, "version": VERSION}).encode(),
            )
            for member in NPM_MEMBERS + additions.get(kind, ()):
                _tar_member(archive, f"package/{member}", b"content\n")


def _build(root: Path) -> dict[str, object]:
    release = root / "release"
    _write_artifacts(release)
    return build_manifest(root, release, BUILD_CONTEXT)


def test_verify_source_accepts_synchronized_metadata(tmp_path: Path) -> None:
    _write_source(tmp_path)
    contract = verify_source(tmp_path, f"v{VERSION}")
    assert contract.version == VERSION


def test_verify_source_rejects_stale_documented_action_version(tmp_path: Path) -> None:
    _write_source(tmp_path)
    (tmp_path / "README.md").write_text(
        "- uses: franklinbaldo/okf-parser@v1.2.2\n", encoding="utf-8"
    )
    with pytest.raises(ContractError, match="GitHub Action example"):
        verify_source(tmp_path)


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
    artifact = cast("dict[str, object]", first)
    target = tmp_path / "release" / str(artifact["path"])
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(ContractError, match="size mismatch"):
        verify_local(tmp_path, tmp_path / "release" / "manifest.json")


def test_build_manifest_rejects_unexpected_artifact(tmp_path: Path) -> None:
    _write_source(tmp_path)
    release = tmp_path / "release"
    _write_artifacts(release)
    (release / "npm" / "extra.tgz").write_bytes(b"extra")
    with pytest.raises(ContractError, match="unexpected release artifacts"):
        build_manifest(tmp_path, release, BUILD_CONTEXT)


def test_verify_local_rejects_path_traversal(tmp_path: Path) -> None:
    _write_source(tmp_path)
    _build(tmp_path)
    path = tmp_path / "release" / "manifest.json"
    raw_manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest = cast("dict[str, object]", raw_manifest)
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    first = artifacts[0]
    assert isinstance(first, dict)
    artifact = cast("dict[str, object]", first)
    artifact["path"] = "../escape.whl"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ContractError, match="unsafe"):
        verify_local(tmp_path, path)


def test_verify_contents_accepts_complete_distributions(tmp_path: Path) -> None:
    _write_source(tmp_path)
    _build(tmp_path)
    report = verify_contents(tmp_path, tmp_path / "release" / "manifest.json")
    assert report["version"] == VERSION
    artifacts = report["artifacts"]
    assert isinstance(artifacts, dict)
    assert set(artifacts) == {"python-wheel", "python-sdist", "npm-parser", "npm-duckdb"}


def test_verify_contents_rejects_missing_module(tmp_path: Path) -> None:
    _write_source(tmp_path)
    release = tmp_path / "release"
    _write_artifacts(release)
    wheel = release / "python" / f"okf_parser-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        kept = [name for name in archive.namelist() if not name.endswith("cli.py")]
        contents = {name: archive.read(name) for name in kept}
    with zipfile.ZipFile(wheel, mode="w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    build_manifest(tmp_path, release, BUILD_CONTEXT)
    with pytest.raises(ContractError, match=re.escape("missing okf_parser/cli.py")):
        verify_contents(tmp_path, release / "manifest.json")


@pytest.mark.parametrize(
    ("kind", "member", "reason"),
    [
        ("python-sdist", ".github/workflows/release.yml", "excluded directory"),
        ("python-sdist", "src/okf_parser/__pycache__/cli.pyc", "excluded directory"),
        ("npm-parser", "src/index.ts", "source-only or development path"),
        ("npm-duckdb", ".npmrc", "excluded file name"),
        ("python-wheel", "okf_parser/signing.pem", "excluded file type"),
    ],
)
def test_verify_contents_rejects_unshippable_members(
    tmp_path: Path, kind: str, member: str, reason: str
) -> None:
    _write_source(tmp_path)
    release = tmp_path / "release"
    _write_artifacts(release, {kind: (member,)})
    build_manifest(tmp_path, release, BUILD_CONTEXT)
    with pytest.raises(ContractError, match=reason):
        verify_contents(tmp_path, release / "manifest.json")


def test_verify_contents_rejects_members_outside_package_root(tmp_path: Path) -> None:
    _write_source(tmp_path)
    release = tmp_path / "release"
    _write_artifacts(release)
    tarball = release / "npm" / f"okf-parser-{VERSION}.tgz"
    with tarfile.open(tarball, mode="r:gz") as archive:
        members = [(member.name, _tar_bytes(archive, member)) for member in archive.getmembers()]
    with tarfile.open(tarball, mode="w:gz") as archive:
        for name, data in members:
            _tar_member(archive, name, data)
        _tar_member(archive, "outside/leak.txt", b"content\n")
    build_manifest(tmp_path, release, BUILD_CONTEXT)
    with pytest.raises(ContractError, match="member outside"):
        verify_contents(tmp_path, release / "manifest.json")
