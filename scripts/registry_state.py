"""Inspect public package registries without performing any mutation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, Never, cast
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
USER_AGENT: Final = "okf-parser-release-contract/1"
PYPI_BASE: Final = "https://pypi.org/pypi"
NPM_BASE: Final = "https://registry.npmjs.org"
HTTP_OK: Final = 200
HTTP_NOT_FOUND: Final = 404

Artifact = dict[str, object]
RegistryEntry = dict[str, object]
RegistryReport = dict[str, object]


class RegistryStateError(ValueError):
    """Report an invalid manifest or unreadable registry response."""


@dataclass(frozen=True)
class HttpResult:
    """Bounded HTTP response used by registry inspection and test doubles."""

    status: int
    body: bytes
    url: str


@dataclass(frozen=True)
class RegistryTarget:
    """One registry package/version endpoint pair."""

    registry: str
    package: str
    version: str
    version_url: str
    package_url: str


Fetch = Callable[[str, float], HttpResult]


def _fail(message: str) -> Never:
    raise RegistryStateError(message)


def _read_bounded(stream: BinaryIO) -> bytes:
    data = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        _fail(f"registry response exceeds {MAX_RESPONSE_BYTES} bytes")
    return data


def fetch_https(url: str, timeout: float) -> HttpResult:
    """Fetch one public HTTPS registry document with a strict size limit."""
    if not url.startswith("https://"):
        _fail(f"registry URL must use HTTPS: {url!r}")
    request = Request(  # noqa: S310 -- HTTPS is enforced immediately above.
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return HttpResult(
                status=response.status,
                body=_read_bounded(cast("BinaryIO", response)),
                url=response.geturl(),
            )
    except HTTPError as exc:
        return HttpResult(
            status=exc.code,
            body=_read_bounded(cast("BinaryIO", exc)),
            url=url,
        )
    except OSError as exc:
        message = f"cannot fetch {url}: {exc}"
        raise RegistryStateError(message) from exc


def _json_object(result: HttpResult) -> dict[str, object]:
    try:
        value = json.loads(result.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot decode registry JSON from {result.url}: {exc}")
    if not isinstance(value, dict):
        _fail(f"registry response from {result.url} is not an object")
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


# Not a fixed enum here on purpose: registry_state.py stays decoupled from
# release_contract.py's exact wheel-kind names (one per supported platform,
# e.g. "python-wheel-linux-x86_64") so adding/removing a target platform
# never requires touching this file. Any kind other than the two npm
# packages is treated as a PyPI artifact for okf-parser.
_NPM_KINDS: Final = {"npm-parser", "npm-duckdb"}
_MIN_PYPI_ARTIFACTS: Final = 2  # at least one wheel + the sdist


def _artifact_index(manifest: dict[str, object]) -> tuple[str, dict[str, Artifact]]:
    version = manifest.get("version")
    artifacts = manifest.get("artifacts")
    if not isinstance(version, str) or not version:
        _fail("manifest version must be a non-empty string")
    if not isinstance(artifacts, list):
        _fail("manifest artifacts must be a list")
    indexed: dict[str, Artifact] = {}
    for raw in artifacts:
        item = _mapping(raw, "manifest artifact")
        kind = _string(item, "kind", "manifest artifact")
        if kind in indexed:
            _fail(f"duplicate manifest artifact kind {kind!r}")
        indexed[kind] = item
    if not _NPM_KINDS.issubset(indexed):
        _fail("manifest artifact set is missing an npm package")
    pypi_kinds = set(indexed) - _NPM_KINDS
    if len(pypi_kinds) < _MIN_PYPI_ARTIFACTS:
        _fail("manifest artifact set is missing a PyPI wheel or sdist")
    if not any(kind == "python-sdist" for kind in pypi_kinds):
        _fail("manifest artifact set is missing the python-sdist")
    return version, indexed


def _probe_package(url: str, fetch: Fetch, timeout: float) -> tuple[bool | None, str]:
    try:
        result = fetch(url, timeout)
    except RegistryStateError as exc:
        return None, str(exc)
    if result.status == HTTP_NOT_FOUND:
        return False, "package name is currently unregistered"
    if result.status == HTTP_OK:
        return True, "package already exists"
    return None, f"package lookup returned HTTP {result.status}"


def _absent_entry(target: RegistryTarget, fetch: Fetch, timeout: float) -> RegistryEntry:
    package_exists, bootstrap_reason = _probe_package(target.package_url, fetch, timeout)
    return {
        "registry": target.registry,
        "package": target.package,
        "version": target.version,
        "state": "absent",
        "reason": "target version is not published",
        "version_url": target.version_url,
        "package_exists": package_exists,
        "bootstrap_reason": bootstrap_reason,
    }


def _unverifiable_entry(
    *, registry: str, package: str, version: str, url: str, reason: str
) -> RegistryEntry:
    return {
        "registry": registry,
        "package": package,
        "version": version,
        "state": "unverifiable",
        "reason": reason,
        "version_url": url,
        "package_exists": None,
    }


def _pypi_expected_files(artifacts: dict[str, Artifact]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for kind, item in artifacts.items():
        if kind in _NPM_KINDS:
            continue
        expected[_string(item, "filename", kind)] = _string(item, "sha256", kind)
    return expected


def _pypi_digests(document: dict[str, object]) -> dict[str, str]:
    urls = document.get("urls")
    if not isinstance(urls, list):
        _fail("PyPI response has no urls list")
    digests: dict[str, str] = {}
    for raw in urls:
        item = _mapping(raw, "PyPI file")
        filename = _string(item, "filename", "PyPI file")
        digest_map = _mapping(item.get("digests"), f"PyPI file {filename}.digests")
        digests[filename] = _string(digest_map, "sha256", f"PyPI file {filename}.digests")
    return digests


def _pypi_state(
    version: str,
    artifacts: dict[str, Artifact],
    fetch: Fetch,
    timeout: float,
) -> RegistryEntry:
    package = "okf-parser"
    version_url = f"{PYPI_BASE}/{package}/{version}/json"
    package_url = f"{PYPI_BASE}/{package}/json"
    try:
        result = fetch(version_url, timeout)
    except RegistryStateError as exc:
        return _unverifiable_entry(
            registry="pypi", package=package, version=version, url=version_url, reason=str(exc)
        )
    if result.status == HTTP_NOT_FOUND:
        target = RegistryTarget("pypi", package, version, version_url, package_url)
        return _absent_entry(target, fetch, timeout)
    if result.status != HTTP_OK:
        return _unverifiable_entry(
            registry="pypi",
            package=package,
            version=version,
            url=version_url,
            reason=f"version lookup returned HTTP {result.status}",
        )
    try:
        actual = _pypi_digests(_json_object(result))
        expected = _pypi_expected_files(artifacts)
    except RegistryStateError as exc:
        return _unverifiable_entry(
            registry="pypi", package=package, version=version, url=version_url, reason=str(exc)
        )
    missing = sorted(set(expected) - set(actual))
    mismatched = sorted(name for name, digest in expected.items() if actual.get(name) != digest)
    if missing or mismatched:
        details = []
        if missing:
            details.append(f"missing files: {', '.join(missing)}")
        if mismatched:
            details.append(f"digest mismatch: {', '.join(mismatched)}")
        return {
            "registry": "pypi",
            "package": package,
            "version": version,
            "state": "present_conflict",
            "reason": "; ".join(details),
            "version_url": version_url,
            "package_exists": True,
        }
    return {
        "registry": "pypi",
        "package": package,
        "version": version,
        "state": "present_expected",
        "reason": "wheel and source distribution match the manifest",
        "version_url": version_url,
        "package_exists": True,
    }


def _npm_state(
    package: str,
    artifact: Artifact,
    fetch: Fetch,
    timeout: float,
) -> RegistryEntry:
    version = _string(artifact, "version", package)
    encoded = quote(package, safe="")
    version_url = f"{NPM_BASE}/{encoded}/{quote(version, safe='')}"
    package_url = f"{NPM_BASE}/{encoded}"
    try:
        result = fetch(version_url, timeout)
    except RegistryStateError as exc:
        return _unverifiable_entry(
            registry="npm", package=package, version=version, url=version_url, reason=str(exc)
        )
    if result.status == HTTP_NOT_FOUND:
        target = RegistryTarget("npm", package, version, version_url, package_url)
        return _absent_entry(target, fetch, timeout)
    if result.status != HTTP_OK:
        return _unverifiable_entry(
            registry="npm",
            package=package,
            version=version,
            url=version_url,
            reason=f"version lookup returned HTTP {result.status}",
        )
    try:
        document = _json_object(result)
        if _string(document, "name", "npm version") != package:
            _fail("npm package identity differs from the manifest")
        if _string(document, "version", "npm version") != version:
            _fail("npm version identity differs from the manifest")
        distribution = _mapping(document.get("dist"), "npm version.dist")
        actual = _string(distribution, "integrity", "npm version.dist")
        expected = _string(artifact, "sri", package)
    except RegistryStateError as exc:
        return _unverifiable_entry(
            registry="npm", package=package, version=version, url=version_url, reason=str(exc)
        )
    if actual != expected:
        return {
            "registry": "npm",
            "package": package,
            "version": version,
            "state": "present_conflict",
            "reason": "registry integrity differs from the manifest",
            "version_url": version_url,
            "package_exists": True,
        }
    return {
        "registry": "npm",
        "package": package,
        "version": version,
        "state": "present_expected",
        "reason": "registry integrity matches the manifest",
        "version_url": version_url,
        "package_exists": True,
    }


def _action(state: object) -> str:
    if state == "absent":
        return "publish"
    if state == "present_expected":
        return "skip"
    return "block"


def inspect_registry_state(
    manifest: dict[str, object],
    *,
    fetch: Fetch = fetch_https,
    timeout: float = 15.0,
) -> RegistryReport:
    """Classify PyPI and npm state for every artifact in a verified manifest."""
    if timeout <= 0:
        _fail("registry timeout must be positive")
    version, artifacts = _artifact_index(manifest)
    entries = [
        _pypi_state(version, artifacts, fetch, timeout),
        _npm_state("okf-parser", artifacts["npm-parser"], fetch, timeout),
        _npm_state("okf-parser-duckdb", artifacts["npm-duckdb"], fetch, timeout),
    ]
    states = [entry["state"] for entry in entries]
    allowed = {"absent", "present_expected"}
    return {
        "schema_version": 1,
        "version": version,
        "safe_to_publish": all(state in allowed for state in states),
        "complete": all(state == "present_expected" for state in states),
        "entries": entries,
        "plan": [
            {
                "registry": entry["registry"],
                "package": entry["package"],
                "action": _action(entry["state"]),
            }
            for entry in entries
        ],
    }


def write_registry_report(path: Path, report: RegistryReport) -> None:
    """Write one deterministic registry-state report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read manifest {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"manifest {path} is not a JSON object")
    return cast("dict[str, object]", value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("release/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("release/registry-state.json"))
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the read-only registry preflight CLI."""
    arguments = _parser().parse_args(argv)
    try:
        report = inspect_registry_state(
            _read_manifest(arguments.manifest.resolve()),
            timeout=arguments.timeout,
        )
        write_registry_report(arguments.output.resolve(), report)
    except RegistryStateError as exc:
        sys.stderr.write(f"registry preflight error: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if report["safe_to_publish"] is True else 3


if __name__ == "__main__":
    raise SystemExit(main())
