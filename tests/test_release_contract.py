"""Tests for synchronized release metadata and artifact verification."""

from __future__ import annotations

import io
import json
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import cast

import pytest

from scripts.release_contract import (
    KINDS,
    WHEEL_KINDS,
    BuildContext,
    ContractError,
    build_manifest,
    verify_contents,
    verify_local,
    verify_source,
)

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
    (root / "native-npm-linux-x64").mkdir()
    (root / "changelog").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "okf-parser"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (root / "typescript" / "package.json").write_text(
        json.dumps(
            {
                "name": "@franklinbaldo/okf-parser",
                "version": VERSION,
                "optionalDependencies": {"@franklinbaldo/okf-parser-native-linux-x64": VERSION},
            }
        ),
        encoding="utf-8",
    )
    (root / "native-npm-linux-x64" / "package.json").write_text(
        json.dumps({"name": "@franklinbaldo/okf-parser-native-linux-x64", "version": VERSION}),
        encoding="utf-8",
    )
    (root / "typescript-duckdb" / "package.json").write_text(
        json.dumps(
            {
                "name": "@franklinbaldo/okf-parser-duckdb",
                "version": VERSION,
                "peerDependencies": {"@franklinbaldo/okf-parser": f"^{VERSION}"},
            }
        ),
        encoding="utf-8",
    )
    (root / "typescript" / "src" / "version.ts").write_text(
        f'export const PROTOCOL_VERSION = "{protocol_version}";\n', encoding="utf-8"
    )
    (root / "changelog" / VERSION).mkdir()
    (root / "changelog" / VERSION / "test-note.md").write_text(
        "---\ntype: Release Note\ntitle: Test\n---\n\n- Test.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        f"- uses: franklinbaldo/okf-parser@v{VERSION}\n", encoding="utf-8"
    )
    (root / "rust-core" / "src").mkdir(parents=True)
    (root / "okf-engine" / "src").mkdir(parents=True)
    (root / "rust-core" / "Cargo.toml").write_text(
        "[package]\n"
        'name = "okf-core"\n'
        f'version = "{VERSION}"\n'
        "\n[dependencies]\n"
        f'okf-engine = {{ path = "../okf-engine", version = "{VERSION}" }}\n',
        encoding="utf-8",
    )
    (root / "okf-engine" / "Cargo.toml").write_text(
        f'[package]\nname = "okf-engine"\nversion = "{VERSION}"\n', encoding="utf-8"
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
NPM_NATIVE_MEMBERS = ("README.md", "bin/okf-core")
NPM_MEMBERS = (
    "README.md",
    "LICENSE",
    "dist/index.js",
    "dist/index.d.ts",
    "dist/cli.js",
    "dist/mcp.js",
)
# One concrete, glob-matching filename per platform wheel kind — see
# WHEEL_PATTERNS in scripts/release_contract.py for the patterns these must
# satisfy. Real CI fills in the exact manylinux/macOS policy numbers; tests
# only need *a* filename each pattern accepts.
WHEEL_FILENAMES: dict[str, str] = {
    "python-wheel-linux-x86_64": f"okf_parser-{VERSION}-py3-none-manylinux_2_28_x86_64.whl",
    "python-wheel-linux-aarch64": f"okf_parser-{VERSION}-py3-none-manylinux_2_28_aarch64.whl",
    "python-wheel-windows-x86_64": f"okf_parser-{VERSION}-py3-none-win_amd64.whl",
    "python-wheel-macos-x86_64": f"okf_parser-{VERSION}-py3-none-macosx_10_12_x86_64.whl",
    "python-wheel-macos-arm64": f"okf_parser-{VERSION}-py3-none-macosx_11_0_arm64.whl",
}


def _write_artifacts(release: Path, extra: dict[str, tuple[str, ...]] | None = None) -> None:
    additions = extra or {}
    python_dir = release / "python"
    npm_dir = release / "npm"
    native_dir = release / "native-npm"
    python_dir.mkdir(parents=True)
    npm_dir.mkdir()
    native_dir.mkdir()
    for kind in WHEEL_KINDS:
        wheel = python_dir / WHEEL_FILENAMES[kind]
        with zipfile.ZipFile(wheel, mode="w") as archive:
            archive.writestr(
                f"okf_parser-{VERSION}.dist-info/METADATA",
                f"Metadata-Version: 2.4\nName: okf-parser\nVersion: {VERSION}\n",
            )
            for member in WHEEL_MEMBERS + additions.get(kind, ()):
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
    with tarfile.open(
        native_dir / f"franklinbaldo-okf-parser-native-linux-x64-{VERSION}.tgz", mode="w:gz"
    ) as archive:
        _tar_member(
            archive,
            "package/package.json",
            json.dumps(
                {"name": "@franklinbaldo/okf-parser-native-linux-x64", "version": VERSION}
            ).encode(),
        )
        for member in NPM_NATIVE_MEMBERS + additions.get("npm-native", ()):
            _tar_member(archive, f"package/{member}", b"content\n")
    # `npm pack` flattens a scope into the leading file-name segment, so the
    # published name and the tarball name differ for every scoped package.
    kinds = {
        "@franklinbaldo/okf-parser": ("npm-parser", "franklinbaldo-okf-parser"),
        "@franklinbaldo/okf-parser-duckdb": ("npm-duckdb", "franklinbaldo-okf-parser-duckdb"),
    }
    for package, (kind, stem) in kinds.items():
        with tarfile.open(npm_dir / f"{stem}-{VERSION}.tgz", mode="w:gz") as archive:
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


def test_verify_source_rejects_stale_native_optional_dependency(tmp_path: Path) -> None:
    _write_source(tmp_path)
    parser_path = tmp_path / "typescript" / "package.json"
    parser = json.loads(parser_path.read_text(encoding="utf-8"))
    parser["optionalDependencies"]["@franklinbaldo/okf-parser-native-linux-x64"] = "1.2.2"
    parser_path.write_text(json.dumps(parser), encoding="utf-8")
    with pytest.raises(ContractError, match="native optional dependency"):
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
    assert len(artifacts) == len(KINDS)
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
    assert set(artifacts) == set(KINDS)


def test_verify_contents_rejects_missing_module(tmp_path: Path) -> None:
    _write_source(tmp_path)
    release = tmp_path / "release"
    _write_artifacts(release)
    wheel = release / "python" / WHEEL_FILENAMES["python-wheel-linux-x86_64"]
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
        ("npm-native", ".npmrc", "excluded file name"),
        ("python-wheel-linux-x86_64", "okf_parser/signing.pem", "excluded file type"),
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
    tarball = release / "npm" / f"franklinbaldo-okf-parser-{VERSION}.tgz"
    with tarfile.open(tarball, mode="r:gz") as archive:
        members = [(member.name, _tar_bytes(archive, member)) for member in archive.getmembers()]
    with tarfile.open(tarball, mode="w:gz") as archive:
        for name, data in members:
            _tar_member(archive, name, data)
        _tar_member(archive, "outside/leak.txt", b"content\n")
    build_manifest(tmp_path, release, BUILD_CONTEXT)
    with pytest.raises(ContractError, match="member outside"):
        verify_contents(tmp_path, release / "manifest.json")


def test_verify_source_rejects_drifted_engine_crate(tmp_path: Path) -> None:
    _write_source(tmp_path)
    (tmp_path / "okf-engine" / "Cargo.toml").write_text(
        '[package]\nname = "okf-engine"\nversion = "0.39.1"\n', encoding="utf-8"
    )
    with pytest.raises(ContractError, match="crate version"):
        verify_source(tmp_path)


def test_verify_source_rejects_drifted_core_crate(tmp_path: Path) -> None:
    _write_source(tmp_path)
    (tmp_path / "rust-core" / "Cargo.toml").write_text(
        '[package]\nname = "okf-core"\nversion = "0.39.1"\n\n[dependencies]\n'
        f'okf-engine = {{ path = "../okf-engine", version = "{VERSION}" }}\n',
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="crate version"):
        verify_source(tmp_path)


def test_verify_source_rejects_stale_internal_crate_dependency(tmp_path: Path) -> None:
    """#172: rust-core pins okf-engine; the pin must move with the workspace."""
    _write_source(tmp_path)
    (tmp_path / "rust-core" / "Cargo.toml").write_text(
        f'[package]\nname = "okf-core"\nversion = "{VERSION}"\n\n[dependencies]\n'
        'okf-engine = { path = "../okf-engine", version = "0.39.1" }\n',
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="internal okf-engine dependency"):
        verify_source(tmp_path)


def test_repository_rust_crates_track_workspace_version() -> None:
    """Regression guard for #172 against the real tree.

    okf-engine slept at 0.39.1 for six releases while the workspace published
    0.45.0 because no check ever read a Cargo.toml. This test fails on that
    un-bumped state — the real drift is the fixture.
    """
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = cast("dict[str, object]", pyproject["project"])["version"]
    for crate in ("rust-core", "okf-engine"):
        manifest = tomllib.loads((root / crate / "Cargo.toml").read_text(encoding="utf-8"))
        assert cast("dict[str, object]", manifest["package"])["version"] == version, (
            f"{crate}/Cargo.toml drifted from workspace version {version}"
        )
