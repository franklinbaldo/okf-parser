# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "okf-parser>=0.45.4,<0.46",
# ]
# ///
# ruff: noqa: T201
"""Query a codebase-to-OKF projection through okf-parser's generic Bundle API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from okf_parser import load_bundle


class QueryError(ValueError):
    """Expected user-facing query failure."""


def fail(message: str) -> None:
    """Raise an expected query failure with a prebuilt message."""
    raise QueryError(message)


def _rows(bundle_path: Path) -> list[dict[str, Any]]:
    """Load concept rows and expose producer frontmatter as query fields."""
    bundle = load_bundle(bundle_path)
    if not bundle.is_conformant:
        fail("bundle is not conformant")

    result: list[dict[str, Any]] = []
    for row in bundle.concepts.execute().to_dict(orient="records"):
        frontmatter = json.loads(row["frontmatter_json"])
        result.append(
            {
                "path": row["path"],
                "type": row["concept_type"],
                "title": row["title"],
                "body": row["body"],
                **frontmatter,
            }
        )
    return result


def _contains(value: object, needle: str) -> bool:
    """Apply case-insensitive containment to scalar or list metadata."""
    wanted = needle.casefold()
    if isinstance(value, list):
        return any(wanted in str(item).casefold() for item in value)
    return wanted in str(value or "").casefold()


def _matches(row: dict[str, Any], args: argparse.Namespace) -> bool:
    """Apply exact-domain filters followed by an optional broad text query."""
    filters = {
        "type": args.type,
        "name": args.name,
        "caller": args.caller,
        "callee": args.callee,
        "source_path": args.source,
        "resolved_modules": args.dependency,
    }
    for key, value in filters.items():
        if value and not _contains(row.get(key), value):
            return False

    if not args.query:
        return True

    searchable = (
        "title",
        "name",
        "qualname",
        "signature",
        "caller",
        "callee",
        "expression",
        "source_path",
        "parameters",
        "fields",
        "bases",
        "decorators",
        "calls_raw",
        "targets",
        "resolved_modules",
        "unresolved_targets",
        "resolution_method",
    )
    return any(_contains(row.get(key), args.query) for key in searchable)


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    """Return only metadata that commonly helps an agent decide the next read."""
    keys = (
        "path",
        "type",
        "title",
        "name",
        "qualname",
        "source_path",
        "line_start",
        "line_end",
        "signature",
        "return_annotation",
        "caller",
        "caller_line_start",
        "callee",
        "expression",
        "source_import",
        "targets",
        "resolved_modules",
        "unresolved_targets",
        "resolution",
        "resolution_method",
        "candidate_targets",
    )
    return {key: row[key] for key in keys if row.get(key) not in (None, "", [])}


def build_parser() -> argparse.ArgumentParser:
    """Build a small code-domain query CLI without adding code semantics to core."""
    parser = argparse.ArgumentParser(description="Query a codebase-to-OKF derived bundle.")
    parser.add_argument("bundle", type=Path, help="generated OKF bundle")
    parser.add_argument("query", nargs="?", default="", help="broad text query")
    parser.add_argument("--type", help="filter by producer-defined concept type")
    parser.add_argument("--name", help="filter by symbol name")
    parser.add_argument("--caller", help="filter call observations by caller")
    parser.add_argument("--callee", help="filter call observations by callee")
    parser.add_argument("--source", help="filter by source-relative path")
    parser.add_argument(
        "--dependency",
        help="filter source-tree import resolutions by local module",
    )
    parser.add_argument("--limit", type=int, default=10, help="maximum results")
    parser.add_argument(
        "--full",
        action="store_true",
        help="include complete frontmatter and body rather than compact metadata",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a deterministic local query and print compact JSON for agent use."""
    args = build_parser().parse_args(argv)
    if args.limit < 1:
        print("error: --limit must be positive", file=sys.stderr)
        return 2

    try:
        rows = _rows(args.bundle.resolve())
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    matches = [row for row in rows if _matches(row, args)][: args.limit]
    payload = matches if args.full else [_compact(row) for row in matches]
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
