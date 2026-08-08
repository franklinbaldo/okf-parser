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
ACTION_REF: Final = re.compile(
    r"franklinbaldo/okf-parser@v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))"
)
PROTOCOL_VERSION: Final = re.compile(
    r'^export const PROTOCOL_VERSION = "(?P<version>[^"]+)";\s*$', re.MULTILINE
)
KINDS: Final = ("python-wheel", "python-sdist", "npm-parser", "npm-duckdb")
Kind = Literal["python-wheel", "python-sdist", "npm-parser", "npm-duckdb"]
Artifact = dict[str, object]
ROOT_PREFIXES: Final[dict[str, str | None]] = {
    "python-wheel": None,
    "python-sdist": "okf_parser-{version}/",
    "npm-parser": "package/",
    "npm-duckdb": "package/",
}
FORBIDDEN_DIRECTORIES: Final = frozenset(
    {
        ".git",
        ".github",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".vscode",
        "__pycache__",
        "htmlcov",
        "node_modules",
        "venv",
    }
)
FORBIDDEN_NAMES: Final = frozenset(
    {
        ".DS_Store",
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
    }
)
FORBIDDEN_SUFFIXES: Final = frozenset(
    {".duckdb", ".key", ".p12", ".pem", ".pfx", ".pyc", ".pyo", ".sqlite"}
)


class ContractError(ValueError):
    """Report a source or artifact contract violation."""


@dataclass(frozen=True)
class SourceContract:
    """Synchronized source identities for one version."""

    version: str
    changelog: str


@dataclass(frozen=True)
class BuildContext:
    """Provenance recorded for one artifact build."""

    repository: str
    commit: str
    ref: str
    tools: dict[str, str]


@dataclass(frozen=True)
class ExpectedArtifact:
    """Expected artifact identity and location."""

    kind: Kind
    package: str
    directory: str
    filename: str | None = None
    pattern: str | None = None


@dataclass(frozen=True)
class ContentPolicy:
    """Files one distribution must ship and paths it must never ship."""

    required: tuple[str, ...]
    forbidden_prefixes: tuple[str, ...] = ()


CONTENT_POLICIES: Final[dict[str, ContentPolicy]] = {
    "python-wheel": ContentPolicy(
        required=("okf_parser/__init__.py", "okf_parser/cli.py", "okf_parser/parser.py"),
        forbidden_prefixes=("tests/", "scripts/", "typescript/", "typescript-duckdb/"),
    ),
    "python-sdist": ContentPolicy(
        required=("PKG-INFO", "README.md", "pyproject.toml", "src/okf_parser/__init__.py"),
        forbidden_prefixes=(),
    ),
    "npm-parser": ContentPolicy(
        required=(
            "package.json",
            "README.md",
            "LICENSE",
            "dist/index.js",
            "dist/index.d.ts",
            "dist/cli.js",
            "dist/mcp.js",
        ),
        forbidden_prefixes=("src/", "test/", "scripts/", "tsconfig"),
    ),
    "npm-duckdb": ContentPolicy(
        required=(
            "package.json",
            "README.md",
            "LICENSE",
            "dist/index.js",
            "dist/index.d.ts",
            "dist/cli.js",
        ),
        forbidden_prefixes=("src/", "test/", "scripts/", "tsconfig"),
    ),
}


@dataclass(frozen=True)
class VerificationContext:
    """Shared state for local artifact verification."""

    release: Path
    version: str
    expected: dict[Kind, ExpectedArtifact]


def _fail(message: str) -> Never:
    raise ContractError(message)


def _decode_json_object(data: str, label: str) -> dict[str, object]:
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        _fail(f"cannot decode JSON from {label}: {exc}")
    if not isinstance(value, dict):
        _fail(f"expected a JSON object in {label}")
    return cast("dict[str, object]", value)


def _json(path: Path) -> dict[str, object]:
    try:
        data = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"cannot read JSON from {path}: {exc}")
    return _decode_json_object(data, str(path))


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


def _project_version(root: Path) -> str:
    path = root / "pyproject.toml"
    try:
        pyproject = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        _fail(f"cannot read {path}: {exc}")
    project = _mapping(pyproject.get("project"), "project")
    if _string(project, "name", "project") != "okf-parser":
        _fail("Python package name must be 'okf-parser'")
    version = _string(project, "version", "project")
    if STABLE_SEMVER.fullmatch(version) is None:
        _fail(f"version must be stable SemVer, found {version!r}")
    return version


def _verify_npm_manifest(path: Path, package: str, version: str) -> dict[str, object]:
    manifest = _json(path)
    if _string(manifest, "name", str(path)) != package:
        _fail(f"npm package name in {path} must be {package!r}")
    if _string(manifest, "version", str(path)) != version:
        _fail(f"npm version in {path} differs from Python version")
    return manifest


def _verify_npm_contract(root: Path, version: str) -> None:
    parser_path = root / "typescript" / "package.json"
    _verify_npm_manifest(parser_path, "okf-parser", version)
    adapter_path = root / "typescript-duckdb" / "package.json"
    adapter = _verify_npm_manifest(adapter_path, "okf-parser-duckdb", version)
    peers = _mapping(adapter.get("peerDependencies"), "peerDependencies")
    if _string(peers, "okf-parser", "peerDependencies") != f"^{version}":
        _fail(f"adapter peer dependency must be ^{version}")


def _verify_protocol(root: Path, version: str) -> None:
    path = root / "typescript" / "src" / "version.ts"
    try:
        match = PROTOCOL_VERSION.search(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        _fail(f"cannot read {path}: {exc}")
    if match is None or match.group("version") != version:
        _fail("TypeScript protocol version differs from package version")


def _verify_readme_action(root: Path, version: str) -> None:
    path = root / "README.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"cannot read {path}: {exc}")
    versions = [match.group("version") for match in ACTION_REF.finditer(text)]
    if versions != [version]:
        _fail(
            "README GitHub Action example must contain exactly one exact current-version ref "
            f"v{version}; found {versions}"
        )


def _verify_changelog(root: Path, version: str) -> Path:
    path = root / "changelog" / f"{version}.md"
    metadata = _frontmatter(path)
    if metadata.get("type") != "Release" or metadata.get("title") != f"okf-parser {version}":
        _fail(f"changelog metadata does not identify okf-parser {version}")
    return path


def verify_source(root: Path, tag: str | None = None) -> SourceContract:
    """Verify package names, versions, protocol, peer range and changelog."""
    version = _project_version(root)
    _verify_npm_contract(root, version)
    _verify_protocol(root, version)
    changelog = _verify_changelog(root, version)
    _verify_readme_action(root, version)
    if tag is not None and tag != f"v{version}":
        _fail(f"tag must be v{version}, found {tag!r}")
    return SourceContract(version=version, changelog=changelog.relative_to(root).as_posix())


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


def _wheel_identity(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                _fail(f"wheel {path} must contain one METADATA file")
            text = archive.read(names[0]).decode("utf-8")
    except (OSError, UnicodeError, KeyError, zipfile.BadZipFile) as exc:
        _fail(f"cannot inspect {path}: {exc}")
    return _metadata_field(text, "Name"), _metadata_field(text, "Version")


def _tar_metadata(path: Path, kind: Kind) -> str:
    try:
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
            return file_object.read().decode("utf-8")
    except (OSError, UnicodeError, KeyError, tarfile.TarError) as exc:
        _fail(f"cannot inspect {path}: {exc}")


def _tar_names(path: Path, kind: Kind) -> list[str]:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            names: list[str] = []
            for member in archive.getmembers():
                if member.isdir():
                    continue
                if not member.isfile():
                    _fail(f"{kind} archive {path.name} contains non-regular member {member.name!r}")
                names.append(member.name)
    except (OSError, tarfile.TarError) as exc:
        _fail(f"cannot inspect {path}: {exc}")
    return names


def _archive_names(path: Path, kind: Kind) -> list[str]:
    if kind == "python-wheel":
        try:
            with zipfile.ZipFile(path) as archive:
                return [item.filename for item in archive.infolist() if not item.is_dir()]
        except (OSError, zipfile.BadZipFile) as exc:
            _fail(f"cannot inspect {path}: {exc}")
    return _tar_names(path, kind)


def _relative_members(path: Path, kind: Kind, version: str) -> list[str]:
    prefix = ROOT_PREFIXES[kind]
    if prefix is not None:
        prefix = prefix.format(version=version)
    members: list[str] = []
    for name in _archive_names(path, kind):
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            _fail(f"{kind} archive {path.name} contains unsafe member {name!r}")
        if prefix is None:
            members.append(pure.as_posix())
            continue
        if not name.startswith(prefix):
            _fail(f"{kind} archive {path.name} member outside {prefix!r}: {name!r}")
        members.append(name.removeprefix(prefix))
    if not members:
        _fail(f"{kind} archive {path.name} is empty")
    return members


def _forbidden_reason(member: str, policy: ContentPolicy) -> str | None:
    pure = PurePosixPath(member)
    parents = pure.parts[:-1]
    if any(part in FORBIDDEN_DIRECTORIES for part in parents):
        return "excluded directory"
    if pure.name in FORBIDDEN_NAMES:
        return "excluded file name"
    if pure.suffix in FORBIDDEN_SUFFIXES:
        return "excluded file type"
    if any(member.startswith(prefix) for prefix in policy.forbidden_prefixes):
        return "source-only or development path"
    return None


def verify_contents(root: Path, manifest_path: Path) -> dict[str, object]:
    """Verify that every built artifact ships its expected files and nothing else."""
    manifest = _json(manifest_path)
    contract = verify_source(root)
    release = manifest_path.parent
    report: dict[str, object] = {}
    seen: set[Kind] = set()
    for item in _manifest_artifacts(manifest, contract.version):
        kind = _entry_kind(item, seen)
        path = _safe_path(release, _string(item, "path", "manifest artifact"))
        members = _relative_members(path, kind, contract.version)
        policy = CONTENT_POLICIES[kind]
        missing = sorted(set(policy.required) - set(members))
        if missing:
            _fail(f"{kind} archive {path.name} is missing {', '.join(missing)}")
        rejected = sorted(
            f"{member} ({reason})"
            for member in members
            if (reason := _forbidden_reason(member, policy)) is not None
        )
        if rejected:
            _fail(f"{kind} archive {path.name} ships {', '.join(rejected)}")
        report[kind] = {"filename": path.name, "member_count": len(members)}
    if seen != set(KINDS):
        _fail("manifest artifact set is incomplete")
    return {"version": contract.version, "artifacts": report}


def _identity(path: Path, kind: Kind) -> tuple[str, str]:
    if kind == "python-wheel":
        return _wheel_identity(path)
    data = _tar_metadata(path, kind)
    if kind == "python-sdist":
        return _metadata_field(data, "Name"), _metadata_field(data, "Version")
    metadata = _decode_json_object(data, str(path))
    return _string(metadata, "name", str(path)), _string(metadata, "version", str(path))


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


def _artifact_record(
    release: Path,
    path: Path,
    expected: ExpectedArtifact,
    version: str,
) -> Artifact:
    package, archive_version = _identity(path, expected.kind)
    if package != expected.package or archive_version != version:
        _fail(f"archive identity mismatch for {path}")
    sha256 = _hash(path, "sha256")
    sha512 = _hash(path, "sha512")
    return {
        "kind": expected.kind,
        "package": package,
        "version": archive_version,
        "path": path.relative_to(release).as_posix(),
        "filename": path.name,
        "size": path.stat().st_size,
        "sha256": sha256,
        "sha512": sha512,
        "sri": _sri(sha512),
    }


def _reject_unexpected(release: Path, expected_paths: set[Path]) -> None:
    actual_paths = {
        path.resolve()
        for name in ("python", "npm")
        for path in (release / name).iterdir()
        if path.is_file()
    }
    extra = sorted(str(path) for path in actual_paths - expected_paths)
    if extra:
        _fail(f"unexpected release artifacts: {', '.join(extra)}")


def _write_manifest_files(release: Path, manifest: dict[str, object]) -> None:
    release.mkdir(parents=True, exist_ok=True)
    (release / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts = cast("list[Artifact]", manifest["artifacts"])
    sums = "\n".join(
        f"{item['sha256']}  {item['path']}"
        for item in sorted(artifacts, key=lambda value: cast("str", value["path"]))
    )
    (release / "SHA256SUMS").write_text(sums + "\n", encoding="utf-8")


def _verify_build_context(context: BuildContext) -> None:
    if "/" not in context.repository:
        _fail("repository must use owner/name form")
    if FULL_SHA.fullmatch(context.commit) is None:
        _fail("commit must be a lowercase full SHA")
    if not context.ref:
        _fail("ref must not be empty")


def build_manifest(root: Path, release: Path, context: BuildContext) -> dict[str, object]:
    """Inspect release files and write manifest.json plus SHA256SUMS."""
    contract = verify_source(root)
    _verify_build_context(context)
    artifacts: list[Artifact] = []
    expected_paths: set[Path] = set()
    for expected in _expected(contract.version):
        path = _find(release, expected)
        expected_paths.add(path.resolve())
        artifacts.append(_artifact_record(release, path, expected, contract.version))
    _reject_unexpected(release, expected_paths)
    artifacts.sort(key=lambda item: cast("str", item["kind"]))
    manifest: dict[str, object] = {
        "schema_version": 1,
        "repository": context.repository,
        "commit": context.commit,
        "ref": context.ref,
        "version": contract.version,
        "tools": dict(sorted(context.tools.items())),
        "artifacts": artifacts,
    }
    _write_manifest_files(release, manifest)
    return manifest


def _safe_path(release: Path, raw: str) -> Path:
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        _fail(f"manifest artifact path is unsafe: {raw!r}")
    path = release.joinpath(*pure.parts)
    if release.resolve() not in path.resolve().parents:
        _fail(f"manifest artifact escapes release directory: {raw!r}")
    return path


def _manifest_artifacts(manifest: dict[str, object], version: str) -> list[Artifact]:
    if manifest.get("schema_version") != 1 or manifest.get("version") != version:
        _fail("manifest schema or version mismatch")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or FULL_SHA.fullmatch(commit) is None:
        _fail("manifest commit is not a full lowercase SHA")
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != len(KINDS):
        _fail(f"manifest must contain exactly {len(KINDS)} artifacts")
    return [_mapping(raw, "manifest artifact") for raw in raw_artifacts]


def _entry_kind(item: Artifact, seen: set[Kind]) -> Kind:
    raw_kind = _string(item, "kind", "manifest artifact")
    if raw_kind not in KINDS:
        _fail(f"unknown artifact kind {raw_kind!r}")
    kind = raw_kind
    if kind in seen:
        _fail(f"duplicate artifact kind {kind!r}")
    seen.add(kind)
    return kind


def _verify_entry(item: Artifact, context: VerificationContext, seen: set[Kind]) -> None:
    kind = _entry_kind(item, seen)
    path = _safe_path(context.release, _string(item, "path", "manifest artifact"))
    if not path.is_file() or path.name != _string(item, "filename", "manifest artifact"):
        _fail(f"manifest file mismatch for {path}")
    package, version = _identity(path, kind)
    specification = context.expected[kind]
    if package != specification.package or item.get("package") != package:
        _fail(f"package identity mismatch for {path}")
    if version != context.version or item.get("version") != version:
        _fail(f"version mismatch for {path}")
    if item.get("size") != path.stat().st_size:
        _fail(f"size mismatch for {path}")
    sha256 = _hash(path, "sha256")
    sha512 = _hash(path, "sha512")
    if item.get("sha256") != sha256 or item.get("sha512") != sha512:
        _fail(f"digest mismatch for {path}")
    if item.get("sri") != _sri(sha512):
        _fail(f"SRI mismatch for {path}")


def _verify_sums(release: Path, artifacts: list[Artifact]) -> None:
    expected = (
        "\n".join(
            f"{item['sha256']}  {item['path']}"
            for item in sorted(artifacts, key=lambda value: cast("str", value["path"]))
        )
        + "\n"
    )
    try:
        actual = (release / "SHA256SUMS").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"cannot read SHA256SUMS: {exc}")
    if actual != expected:
        _fail("SHA256SUMS does not match manifest.json")


def verify_local(root: Path, manifest_path: Path) -> dict[str, object]:
    """Re-read and verify every artifact named by a local manifest."""
    manifest = _json(manifest_path)
    contract = verify_source(root)
    artifacts = _manifest_artifacts(manifest, contract.version)
    context = VerificationContext(
        release=manifest_path.parent,
        version=contract.version,
        expected={item.kind: item for item in _expected(contract.version)},
    )
    seen: set[Kind] = set()
    for item in artifacts:
        _verify_entry(item, context, seen)
    if seen != set(KINDS):
        _fail("manifest artifact set is incomplete")
    _verify_sums(context.release, artifacts)
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
    contents = commands.add_parser("verify-contents")
    contents.add_argument("--root", type=Path, default=Path.cwd())
    contents.add_argument("--manifest", type=Path, default=Path("release/manifest.json"))
    return parser


def _required(value: str | None, label: str) -> str:
    if value is None:
        _fail(f"{label} is required")
    return value


def _build_context(arguments: argparse.Namespace) -> BuildContext:
    return BuildContext(
        repository=_required(arguments.repository, "repository"),
        commit=_required(arguments.commit, "commit"),
        ref=_required(arguments.ref, "ref"),
        tools={
            "python": arguments.python_version or platform.python_version(),
            "node": arguments.node_version or "unknown",
            "npm": arguments.npm_version or "unknown",
            "uv": arguments.uv_version or "unknown",
        },
    )


def main(argv: list[str] | None = None) -> int:
    """Run the release-contract CLI."""
    arguments = _parser().parse_args(argv)
    root = arguments.root.resolve()
    try:
        if arguments.command == "verify-source":
            result: object = asdict(verify_source(root, arguments.tag))
        elif arguments.command == "build-manifest":
            result = build_manifest(root, arguments.directory.resolve(), _build_context(arguments))
        elif arguments.command == "verify-contents":
            result = verify_contents(root, arguments.manifest.resolve())
        else:
            result = verify_local(root, arguments.manifest.resolve())
    except ContractError as exc:
        sys.stderr.write(f"release contract error: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
