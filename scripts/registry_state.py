"""Inspect public package registries without performing any mutation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, Callable, Final, Literal, Never, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from pathlib import Path

MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
USER_AGENT: Final = "okf-parser-release-contract/1"
PYPI_BASE: Final = "https://pypi.org/pypi"
NPM_BASE: Final = "https://registry.npmjs.org"

RegistryStatus = Literal[
    "absent",
    "present_expected",
    "present_conflict",
    "unverifiable",
]
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
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        # The scheme is validated above; S310 cannot infer that guard.
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
    except (OSError, TimeoutError, URLError) as exc:
        raise RegistryStateError(f"cannot fetch {url}: {exc}") from exc


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
    expected = {"python-wheel", "python-sdist", "npm-parser", "npm-duckdb"}
    if set(indexed) != expected:
        _fail("manifest artifact set is incomplete")
    return version, indexed


def _probe_package(url: str, fetch: Fetch, timeout: float) -> tuple[bool | None, str]:
    try:
        result = fetch(url, timeout)
    except RegistryStateError as exc:
        return None, str(exc)
    if result.status == 404:
        return False, "package name is currently unregistered"
    if result.status == 200:
        return True, "package already exists"
    return None, f"package lookup returned HTTP {result.status}"


def _absent_entry(
    *,
    registry: str,
    package: str,
    version: str,
    version_url: str,
    package_url: str,
    fetch: Fetch,
    timeout: float,
) -> RegistryEntry:
    package_exists, bootstrap_reason = _probe_package(package_url, fetch, timeout)
    return {
        "registry": registry,
        "package": package,
        "version": version,
        "state": "absent",
        "reason": "target version is not published",
        "version_url": version_url,
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
    for kind in ("python-wheel", "python-sdist"):
        item = artifacts[kind]
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
    if result.status == 404:
        return _absent_entry(
            registry="pypi",
            package=package,
            version=version,
            version_url=version_url,
            package_url=package_url,
            fetch=fetch,
            timeout=timeout,
        )
    if result.status != 200:
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
    if result.status == 404:
        return _absent_entry(
            registry="npm",
            package=package,
            version=version,
            version_url=version_url,
            package_url=package_url,
            fetch=fetch,
            timeout=timeout,
        )
    if result.status != 200:
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
