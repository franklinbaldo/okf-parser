"""Tests for read-only PyPI and npm release preflight."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from scripts.registry_state import (
    NPM_BASE,
    PYPI_BASE,
    HttpResult,
    RegistryStateError,
    inspect_registry_state,
)

if TYPE_CHECKING:
    from collections.abc import Callable

VERSION = "1.2.3"
PYPI_VERSION_URL = f"{PYPI_BASE}/okf-parser/{VERSION}/json"
PYPI_PACKAGE_URL = f"{PYPI_BASE}/okf-parser/json"
NPM_PARSER_VERSION_URL = f"{NPM_BASE}/okf-parser/{VERSION}"
NPM_PARSER_PACKAGE_URL = f"{NPM_BASE}/okf-parser"
NPM_DUCKDB_VERSION_URL = f"{NPM_BASE}/okf-parser-duckdb/{VERSION}"
NPM_DUCKDB_PACKAGE_URL = f"{NPM_BASE}/okf-parser-duckdb"


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "version": VERSION,
        "artifacts": [
            {
                "kind": "python-wheel",
                "package": "okf-parser",
                "version": VERSION,
                "filename": f"okf_parser-{VERSION}-py3-none-any.whl",
                "sha256": "a" * 64,
                "sri": "sha512-wheel",
            },
            {
                "kind": "python-sdist",
                "package": "okf-parser",
                "version": VERSION,
                "filename": f"okf_parser-{VERSION}.tar.gz",
                "sha256": "b" * 64,
                "sri": "sha512-sdist",
            },
            {
                "kind": "npm-parser",
                "package": "okf-parser",
                "version": VERSION,
                "filename": f"okf-parser-{VERSION}.tgz",
                "sha256": "c" * 64,
                "sri": "sha512-parser",
            },
            {
                "kind": "npm-duckdb",
                "package": "okf-parser-duckdb",
                "version": VERSION,
                "filename": f"okf-parser-duckdb-{VERSION}.tgz",
                "sha256": "d" * 64,
                "sri": "sha512-duckdb",
            },
        ],
    }


def _result(url: str, status: int, payload: object | None = None) -> HttpResult:
    body = b"" if payload is None else json.dumps(payload).encode()
    return HttpResult(status=status, body=body, url=url)


def _fetcher(routes: dict[str, HttpResult], failures: set[str] | None = None):
    failed = failures or set()

    def fetch(url: str, timeout: float) -> HttpResult:
        assert timeout > 0
        if url in failed:
            raise RegistryStateError(f"network failure for {url}")
        return routes[url]

    return fetch


def _pypi_payload(*, include_sdist: bool = True, wheel_digest: str | None = None) -> object:
    files = [
        {
            "filename": f"okf_parser-{VERSION}-py3-none-any.whl",
            "digests": {"sha256": wheel_digest or "a" * 64},
        }
    ]
    if include_sdist:
        files.append(
            {
                "filename": f"okf_parser-{VERSION}.tar.gz",
                "digests": {"sha256": "b" * 64},
            }
        )
    return {"urls": files}


def _npm_payload(package: str, integrity: str) -> object:
    return {
        "name": package,
        "version": VERSION,
        "dist": {"integrity": integrity},
    }


def _expected_routes() -> dict[str, HttpResult]:
    return {
        PYPI_VERSION_URL: _result(PYPI_VERSION_URL, 200, _pypi_payload()),
        NPM_PARSER_VERSION_URL: _result(
            NPM_PARSER_VERSION_URL,
            200,
            _npm_payload("okf-parser", "sha512-parser"),
        ),
        NPM_DUCKDB_VERSION_URL: _result(
            NPM_DUCKDB_VERSION_URL,
            200,
            _npm_payload("okf-parser-duckdb", "sha512-duckdb"),
        ),
    }


def _entries(report: dict[str, object]) -> list[dict[str, object]]:
    raw = report["entries"]
    assert isinstance(raw, list)
    return [cast("dict[str, object]", item) for item in raw]


def test_absent_versions_report_available_bootstrap_names() -> None:
    routes = {
        PYPI_VERSION_URL: _result(PYPI_VERSION_URL, 404),
        PYPI_PACKAGE_URL: _result(PYPI_PACKAGE_URL, 404),
        NPM_PARSER_VERSION_URL: _result(NPM_PARSER_VERSION_URL, 404),
        NPM_PARSER_PACKAGE_URL: _result(NPM_PARSER_PACKAGE_URL, 404),
        NPM_DUCKDB_VERSION_URL: _result(NPM_DUCKDB_VERSION_URL, 404),
        NPM_DUCKDB_PACKAGE_URL: _result(NPM_DUCKDB_PACKAGE_URL, 404),
    }
    report = inspect_registry_state(_manifest(), fetch=_fetcher(routes))
    entries = _entries(report)
    assert report["safe_to_publish"] is True
    assert report["complete"] is False
    assert [entry["state"] for entry in entries] == ["absent", "absent", "absent"]
    assert [entry["package_exists"] for entry in entries] == [False, False, False]
    assert [item["action"] for item in cast("list[dict[str, object]]", report["plan"])] == [
        "publish",
        "publish",
        "publish",
    ]


def test_absent_version_distinguishes_an_existing_package() -> None:
    routes = {
        PYPI_VERSION_URL: _result(PYPI_VERSION_URL, 404),
        PYPI_PACKAGE_URL: _result(PYPI_PACKAGE_URL, 200, {"info": {"name": "okf-parser"}}),
        NPM_PARSER_VERSION_URL: _result(NPM_PARSER_VERSION_URL, 404),
        NPM_PARSER_PACKAGE_URL: _result(
            NPM_PARSER_PACKAGE_URL, 200, {"name": "okf-parser"}
        ),
        NPM_DUCKDB_VERSION_URL: _result(NPM_DUCKDB_VERSION_URL, 404),
        NPM_DUCKDB_PACKAGE_URL: _result(
            NPM_DUCKDB_PACKAGE_URL, 200, {"name": "okf-parser-duckdb"}
        ),
    }
    report = inspect_registry_state(_manifest(), fetch=_fetcher(routes))
    assert [entry["package_exists"] for entry in _entries(report)] == [True, True, True]
    assert report["safe_to_publish"] is True


def test_complete_release_matches_all_registry_digests() -> None:
    report = inspect_registry_state(_manifest(), fetch=_fetcher(_expected_routes()))
    assert report["complete"] is True
    assert report["safe_to_publish"] is True
    assert [entry["state"] for entry in _entries(report)] == [
        "present_expected",
        "present_expected",
        "present_expected",
    ]
    assert [item["action"] for item in cast("list[dict[str, object]]", report["plan"])] == [
        "skip",
        "skip",
        "skip",
    ]


def test_partial_release_produces_a_resumable_plan() -> None:
    routes = _expected_routes()
    routes[NPM_DUCKDB_VERSION_URL] = _result(NPM_DUCKDB_VERSION_URL, 404)
    routes[NPM_DUCKDB_PACKAGE_URL] = _result(
        NPM_DUCKDB_PACKAGE_URL, 200, {"name": "okf-parser-duckdb"}
    )
    report = inspect_registry_state(_manifest(), fetch=_fetcher(routes))
    assert report["safe_to_publish"] is True
    assert report["complete"] is False
    assert [item["action"] for item in cast("list[dict[str, object]]", report["plan"])] == [
        "skip",
        "skip",
        "publish",
    ]


def test_conflicting_npm_integrity_blocks_publication() -> None:
    routes = _expected_routes()
    routes[NPM_PARSER_VERSION_URL] = _result(
        NPM_PARSER_VERSION_URL,
        200,
        _npm_payload("okf-parser", "sha512-conflict"),
    )
    report = inspect_registry_state(_manifest(), fetch=_fetcher(routes))
    assert report["safe_to_publish"] is False
    parser = _entries(report)[1]
    assert parser["state"] == "present_conflict"
    assert "integrity" in str(parser["reason"])


def test_incomplete_pypi_release_is_a_conflict() -> None:
    routes = _expected_routes()
    routes[PYPI_VERSION_URL] = _result(
        PYPI_VERSION_URL,
        200,
        _pypi_payload(include_sdist=False),
    )
    report = inspect_registry_state(_manifest(), fetch=_fetcher(routes))
    pypi = _entries(report)[0]
    assert report["safe_to_publish"] is False
    assert pypi["state"] == "present_conflict"
    assert "missing files" in str(pypi["reason"])


def test_non_success_registry_response_is_unverifiable() -> None:
    routes = _expected_routes()
    routes[PYPI_VERSION_URL] = _result(PYPI_VERSION_URL, 503)
    report = inspect_registry_state(_manifest(), fetch=_fetcher(routes))
    pypi = _entries(report)[0]
    assert report["safe_to_publish"] is False
    assert pypi["state"] == "unverifiable"
    assert "503" in str(pypi["reason"])


def test_network_failure_is_unverifiable_without_hiding_other_states() -> None:
    routes = _expected_routes()
    report = inspect_registry_state(
        _manifest(),
        fetch=_fetcher(routes, failures={NPM_PARSER_VERSION_URL}),
    )
    entries = _entries(report)
    assert [entry["state"] for entry in entries] == [
        "present_expected",
        "unverifiable",
        "present_expected",
    ]
    assert report["safe_to_publish"] is False


def test_malformed_registry_json_is_unverifiable() -> None:
    routes = _expected_routes()
    routes[NPM_DUCKDB_VERSION_URL] = HttpResult(
        status=200,
        body=b"not-json",
        url=NPM_DUCKDB_VERSION_URL,
    )
    report = inspect_registry_state(_manifest(), fetch=_fetcher(routes))
    assert _entries(report)[2]["state"] == "unverifiable"


def test_invalid_manifest_is_rejected_before_network_access() -> None:
    manifest = _manifest()
    manifest["artifacts"] = []
    with pytest.raises(RegistryStateError, match="incomplete"):
        inspect_registry_state(manifest, fetch=_fetcher({}))
