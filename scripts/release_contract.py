"""Validate synchronized release metadata and artifact bytes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import sys
import tarfile
import tomllib
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal, Never, cast

STABLE_SEMVER: Final = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
FULL_SHA: Final = re.compile(r"^[0-9a-f]{40}$")
PROTOCOL_VERSION: Final = re.compile(
    r'^export const PROTOCOL_VERSION = "(?P<version>[^"]+)";\s*$', re.MULTILINE
)
KINDS: Final = ("python-wheel", "python-sdist", "npm-parser", "npm-duckdb")
Kind = Literal["python-wheel", "python-sdist", "npm-parser", "npm-duckdb"]


class ContractError(ValueError):
    """Report a source or artifact contract violation."""


@dataclass(frozen=True)
class SourceContract:
    """Synchronized source identities for one version."""

    version: str
    changelog: str


@dataclass(frozen=True)
class ExpectedArtifact:
    """Expected artifact identity and location."""

    kind: Kind
    package: str
    directory: str
    filename: str | None = None
    pattern: str | None = None


def _fail(message: str) -> Never:
    raise ContractError(message)


def _json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read JSON from {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"expected a JSON object in {path}")
    return cast("dict[str, object]", value)


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail(f"expected {label} to be an object")
    return cast("dict[str, object]", value)


def _string(mapping: dict[str, object], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        _fail(f"expected {label}.{key} to be a non-empty string")
    return value


def _frontmatter(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        _fail(f"cannot read {path}: {exc}")
    if not lines or lines[0] != "---":
        _fail(f"{path} has no frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        _fail(f"{path} has unterminated frontmatter")
    result: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            _fail(f"unsupported frontmatter line in {path}: {line!r}")
        result[key.strip()] = value.strip()
    return result


def verify_source(root: Path, tag: str | None = None) -> SourceContract:
    """Verify package names, versions, protocol, peer range and changelog."""

    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        _fail(f"cannot read pyproject.toml: {exc}")
    project = _mapping(pyproject.get("project"), "project")
    if _string(project, "name", "project") != "okf-parser":
        _fail("Python package name must be 'okf-parser'")
    version = _string(project, "version", "project")
    if STABLE_SEMVER.fullmatch(version) is None:
        _fail(f"version must be stable SemVer, found {version!r}")

    parser_path = root / "typescript" / "package.json"
    parser = _json(parser_path)
    if _string(parser, "name", str(parser_path)) != "okf-parser":
        _fail("main npm package name must be 'okf-parser'")
    if _string(parser, "version", str(parser_path)) != version:
        _fail("main npm version differs from Python version")

    adapter_path = root / "typescript-duckdb" / "package.json"
    adapter = _json(adapter_path)
    if _string(adapter, "name", str(adapter_path)) != "okf-parser-duckdb":
        _fail("adapter npm package name must be 'okf-parser-duckdb'")
    if _string(adapter, "version", str(adapter_path)) != version:
        _fail("adapter npm version differs from Python version")
    peers = _mapping(adapter.get("peerDependencies"), "peerDependencies")
    if _string(peers, "okf-parser", "peerDependencies") != f"^{version}":
        _fail(f"adapter peer dependency must be ^{version}")

    version_path = root / "typescript" / "src" / "version.ts"
    try:
        match = PROTOCOL_VERSION.search(version_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        _fail(f"cannot read {version_path}: {exc}")
    if match is None or match.group("version") != version:
        _fail("TypeScript protocol version differs from package version")

    changelog_path = root / "changelog" / f"{version}.md"
    metadata = _frontmatter(changelog_path)
    if metadata.get("type") != "Release" or metadata.get("title") != f"okf-parser {version}":
        _fail(f"changelog metadata does not identify okf-parser {version}")
    if tag is not None and tag != f"v{version}":
        _fail(f"tag must be v{version}, found {tag!r}")
    return SourceContract(version=version, changelog=changelog_path.relative_to(root).as_posix())


def _expected(version: str) -> tuple[ExpectedArtifact, ...]:
    return (
        ExpectedArtifact(
            "python-wheel",
            "okf-parser",
            "python",
            pattern=f"okf_parser-{version}-*.whl",
        ),
        ExpectedArtifact(
            "python-sdist",
            "okf-parser",
            "python",
            filename=f"okf_parser-{version}.tar.gz",
        ),
        ExpectedArtifact(
            "npm-parser",
            "okf-parser",
            "npm",
            filename=f"okf-parser-{version}.tgz",
        ),
        ExpectedArtifact(
            "npm-duckdb",
            "okf-parser-duckdb",
            "npm",
            filename=f"okf-parser-duckdb-{version}.tgz",
        ),
    )


def _find(directory: Path, expected: ExpectedArtifact) -> Path:
    parent = directory / expected.directory
    if expected.filename is not None:
        path = parent / expected.filename
        if not path.is_file():
            _fail(f"missing expected artifact {path}")
        return path
    matches = sorted(path for path in parent.glob(expected.pattern or "") if path.is_file())
    if len(matches) != 1:
        _fail(f"expected one {expected.kind}, found {len(matches)}")
    return matches[0]


def _metadata_field(text: str, field: str) -> str:
    prefix = f"{field}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    _fail(f"archive metadata has no {field} field")


def _identity(path: Path, kind: Kind) -> tuple[str, str]:
    try:
        if kind == "python-wheel":
            with zipfile.ZipFile(path) as archive:
                names = [
                    name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
                ]
                if len(names) != 1:
                    _fail(f"wheel {path} must contain one METADATA file")
                text = archive.read(names[0]).decode("utf-8")
            return _metadata_field(text, "Name"), _metadata_field(text, "Version")
        with tarfile.open(path, mode="r:gz") as archive:
            if kind == "python-sdist":
                members = [
                    member for member in archive.getmembers() if member.name.endswith("/PKG-INFO")
                ]
                if len(members) != 1:
                    _fail(f"sdist {path} must contain one PKG-INFO file")
                member = members[0]
            else:
                member = archive.getmember("package/package.json")
            file_object = archive.extractfile(member)
            if file_object is None:
                _fail(f"cannot read package metadata from {path}")
            data = file_object.read().decode("utf-8")
    except (OSError, UnicodeError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        _fail(f"cannot inspect {path}: {exc}")
    if kind == "python-sdist":
        return _metadata_field(data, "Name"), _metadata_field(data, "Version")
    metadata = json.loads(data)
    if not isinstance(metadata, dict):
        _fail(f"npm package metadata in {path} is not an object")
    typed = cast("dict[str, object]", metadata)
    return _string(typed, "name", str(path)), _string(typed, "version", str(path))


def _hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    try:
        with path.open("rb") as file_object:
            for block in iter(lambda: file_object.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        _fail(f"cannot hash {path}: {exc}")
    return digest.hexdigest()


def _sri(sha512: str) -> str:
    return "sha512-" + base64.b64encode(bytes.fromhex(sha512)).decode("ascii")


def build_manifest(
    root: Path,
    release: Path,
    repository: str,
    commit: str,
    ref: str,
    tools: dict[str, str],
) -> dict[str, object]:
    """Inspect release files and write manifest.json plus SHA256SUMS."""

    contract = verify_source(root)
    if "/" not in repository:
        _fail("repository must use owner/name form")
    if FULL_SHA.fullmatch(commit) is None:
        _fail("commit must be a lowercase full SHA")
    if not ref:
        _fail("ref must not be empty")

    artifacts: list[dict[str, object]] = []
    expected_paths: set[Path] = set()
    for expected in _expected(contract.version):
        path = _find(release, expected)
        expected_paths.add(path.resolve())
        package, version = _identity(path, expected.kind)
        if package != expected.package or version != contract.version:
            _fail(f"archive identity mismatch for {path}")
        sha256 = _hash(path, "sha256")
        sha512 = _hash(path, "sha512")
        artifacts.append(
            {
                "kind": expected.kind,
                "package": package,
                "version": version,
                "path": path.relative_to(release).as_posix(),
                "filename": path.name,
                "size": path.stat().st_size,
                "sha256": sha256,
                "sha512": sha512,
                "sri": _sri(sha512),
            }
        )

    actual_paths = {
        path.resolve()
        for name in ("python", "npm")
        for path in (release / name).iterdir()
        if path.is_file()
    }
    extra = sorted(str(path) for path in actual_paths - expected_paths)
    if extra:
        _fail(f"unexpected release artifacts: {', '.join(extra)}")

    artifacts.sort(key=lambda item: cast("str", item["kind"]))
    manifest: dict[str, object] = {
        "schema_version": 1,
        "repository": repository,
        "commit": commit,
        "ref": ref,
        "version": contract.version,
        "tools": dict(sorted(tools.items())),
        "artifacts": artifacts,
    }
    release.mkdir(parents=True, exist_ok=True)
    (release / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sums = "\n".join(
        f"{item['sha256']}  {item['path']}"
        for item in sorted(artifacts, key=lambda value: cast("str", value["path"]))
    )
    (release / "SHA256SUMS").write_text(sums + "\n", encoding="utf-8")
    return manifest


def _safe_path(release: Path, raw: str) -> Path:
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        _fail(f"manifest artifact path is unsafe: {raw!r}")
    path = release.joinpath(*pure.parts)
    if release.resolve() not in path.resolve().parents:
        _fail(f"manifest artifact escapes release directory: {raw!r}")
    return path


def verify_local(root: Path, manifest_path: Path) -> dict[str, object]:
    """Re-read and verify every artifact named by a local manifest."""

    manifest = _json(manifest_path)
    contract = verify_source(root)
    if manifest.get("schema_version") != 1 or manifest.get("version") != contract.version:
        _fail("manifest schema or version mismatch")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or FULL_SHA.fullmatch(commit) is None:
        _fail("manifest commit is not a full lowercase SHA")
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != 4:
        _fail("manifest must contain exactly four artifacts")

    expected = {item.kind: item for item in _expected(contract.version)}
    seen: set[str] = set()
    release = manifest_path.parent
    for raw in raw_artifacts:
        item = _mapping(raw, "manifest artifact")
        kind = _string(item, "kind", "manifest artifact")
        if kind not in KINDS or kind in seen:
            _fail(f"unknown or duplicate artifact kind {kind!r}")
        seen.add(kind)
        typed_kind = cast("Kind", kind)
        path = _safe_path(release, _string(item, "path", "manifest artifact"))
        if not path.is_file() or path.name != _string(item, "filename", "manifest artifact"):
            _fail(f"manifest file mismatch for {path}")
        package, version = _identity(path, typed_kind)
        specification = expected[typed_kind]
        if package != specification.package or item.get("package") != package:
            _fail(f"package identity mismatch for {path}")
        if version != contract.version or item.get("version") != version:
            _fail(f"version mismatch for {path}")
        if item.get("size") != path.stat().st_size:
            _fail(f"size mismatch for {path}")
        sha256 = _hash(path, "sha256")
        sha512 = _hash(path, "sha512")
        if item.get("sha256") != sha256 or item.get("sha512") != sha512:
            _fail(f"digest mismatch for {path}")
        if item.get("sri") != _sri(sha512):
            _fail(f"SRI mismatch for {path}")

    if seen != set(KINDS):
        _fail("manifest artifact set is incomplete")
    expected_sums = "\n".join(
        f"{item['sha256']}  {item['path']}"
        for item in sorted(raw_artifacts, key=lambda value: cast("str", value["path"]))
    ) + "\n"
    try:
        actual_sums = (release / "SHA256SUMS").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"cannot read SHA256SUMS: {exc}")
    if actual_sums != expected_sums:
        _fail("SHA256SUMS does not match manifest.json")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    source = commands.add_parser("verify-source")
    source.add_argument("--root", type=Path, default=Path.cwd())
    source.add_argument("--tag")
    build = commands.add_parser("build-manifest")
    build.add_argument("--root", type=Path, default=Path.cwd())
    build.add_argument("--directory", type=Path, default=Path("release"))
    build.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    build.add_argument("--commit", default=os.environ.get("GITHUB_SHA"))
    build.add_argument("--ref", default=os.environ.get("GITHUB_REF_NAME"))
    for name in ("python", "node", "npm", "uv"):
        build.add_argument(f"--{name}-version")
    local = commands.add_parser("verify-local")
    local.add_argument("--root", type=Path, default=Path.cwd())
    local.add_argument("--manifest", type=Path, default=Path("release/manifest.json"))
    return parser


def _required(value: str | None, label: str) -> str:
    if value is None:
        _fail(f"{label} is required")
    return value


def main(argv: list[str] | None = None) -> int:
    """Run the release-contract CLI."""

    arguments = _parser().parse_args(argv)
    root = arguments.root.resolve()
    try:
        if arguments.command == "verify-source":
            result: object = asdict(verify_source(root, arguments.tag))
        elif arguments.command == "build-manifest":
            tools = {
                "python": arguments.python_version or platform.python_version(),
                "node": arguments.node_version or "unknown",
                "npm": arguments.npm_version or "unknown",
                "uv": arguments.uv_version or "unknown",
            }
            result = build_manifest(
                root,
                arguments.directory.resolve(),
                _required(arguments.repository, "repository"),
                _required(arguments.commit, "commit"),
                _required(arguments.ref, "ref"),
                tools,
            )
        else:
            result = verify_local(root, arguments.manifest.resolve())
    except ContractError as exc:
        sys.stderr.write(f"release contract error: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
