"""Regression coverage for the native npm artifact in registry preflight."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from scripts.registry_state import NPM_BASE, PYPI_BASE, HttpResult, inspect_registry_state

if TYPE_CHECKING:
    from collections.abc import Callable

VERSION = "1.2.3"


def _result(url: str, status: int, payload: object | None = None) -> HttpResult:
    body = b"" if payload is None else json.dumps(payload).encode()
    return HttpResult(status=status, body=body, url=url)


def _fetcher(routes: dict[str, HttpResult]) -> Callable[[str, float], HttpResult]:
    def fetch(url: str, timeout: float) -> HttpResult:
        assert timeout > 0
        return routes[url]

    return fetch


def _npm_payload(package: str, integrity: str) -> object:
    return {
        "name": package,
        "version": VERSION,
        "dist": {"integrity": integrity},
    }


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
            {
                "kind": "npm-native",
                "package": "okf-parser-native-linux-x64",
                "version": VERSION,
                "filename": f"okf-parser-native-linux-x64-{VERSION}.tgz",
                "sha256": "e" * 64,
                "sri": "sha512-native",
            },
        ],
    }


def test_native_npm_package_is_not_mistaken_for_a_pypi_file() -> None:
    pypi_version = f"{PYPI_BASE}/okf-parser/{VERSION}/json"
    parser_version = f"{NPM_BASE}/okf-parser/{VERSION}"
    duckdb_version = f"{NPM_BASE}/okf-parser-duckdb/{VERSION}"
    native_version = f"{NPM_BASE}/okf-parser-native-linux-x64/{VERSION}"
    routes = {
        pypi_version: _result(
            pypi_version,
            200,
            {
                "urls": [
                    {
                        "filename": f"okf_parser-{VERSION}-py3-none-any.whl",
                        "digests": {"sha256": "a" * 64},
                    },
                    {
                        "filename": f"okf_parser-{VERSION}.tar.gz",
                        "digests": {"sha256": "b" * 64},
                    },
                ]
            },
        ),
        parser_version: _result(parser_version, 200, _npm_payload("okf-parser", "sha512-parser")),
        duckdb_version: _result(
            duckdb_version, 200, _npm_payload("okf-parser-duckdb", "sha512-duckdb")
        ),
        native_version: _result(
            native_version,
            200,
            _npm_payload("okf-parser-native-linux-x64", "sha512-native"),
        ),
    }

    report = inspect_registry_state(_manifest(), fetch=_fetcher(routes))
    entries = cast("list[dict[str, object]]", report["entries"])

    assert report["complete"] is True
    assert report["safe_to_publish"] is True
    assert [entry["package"] for entry in entries] == [
        "okf-parser",
        "okf-parser",
        "okf-parser-duckdb",
        "okf-parser-native-linux-x64",
    ]
    assert all(entry["state"] == "present_expected" for entry in entries)


def test_native_npm_integrity_conflict_blocks_publication() -> None:
    pypi_version = f"{PYPI_BASE}/okf-parser/{VERSION}/json"
    parser_version = f"{NPM_BASE}/okf-parser/{VERSION}"
    duckdb_version = f"{NPM_BASE}/okf-parser-duckdb/{VERSION}"
    native_version = f"{NPM_BASE}/okf-parser-native-linux-x64/{VERSION}"
    routes = {
        pypi_version: _result(
            pypi_version,
            200,
            {
                "urls": [
                    {
                        "filename": f"okf_parser-{VERSION}-py3-none-any.whl",
                        "digests": {"sha256": "a" * 64},
                    },
                    {
                        "filename": f"okf_parser-{VERSION}.tar.gz",
                        "digests": {"sha256": "b" * 64},
                    },
                ]
            },
        ),
        parser_version: _result(parser_version, 200, _npm_payload("okf-parser", "sha512-parser")),
        duckdb_version: _result(
            duckdb_version, 200, _npm_payload("okf-parser-duckdb", "sha512-duckdb")
        ),
        native_version: _result(
            native_version,
            200,
            _npm_payload("okf-parser-native-linux-x64", "sha512-conflict"),
        ),
    }

    report = inspect_registry_state(_manifest(), fetch=_fetcher(routes))
    entries = cast("list[dict[str, object]]", report["entries"])
    native = entries[-1]

    assert report["safe_to_publish"] is False
    assert native["package"] == "okf-parser-native-linux-x64"
    assert native["state"] == "present_conflict"
