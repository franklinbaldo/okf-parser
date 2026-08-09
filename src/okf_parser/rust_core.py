"""Experimental batch bridge to the optional Rust Markdown facts core."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, TypedDict, cast

from okf_parser.parser import MarkdownFacts

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def rust_load_bundle(
    root: Path, executable: Path, exclude: Sequence[str] = (), *, read_concurrency: int = 32
) -> object:
    """Run the end-to-end native engine and return its relational payload."""
    command = [str(executable), "load", str(root), "--read-concurrency", str(read_concurrency)]
    for pattern in exclude:
        command.extend(("--exclude", pattern))
    completed = subprocess.run(command, capture_output=True, check=False, text=True)  # noqa: S603
    if completed.returncode != 0:
        message = completed.stderr.strip() or f"okf exited with {completed.returncode}"
        raise RustCoreError(message)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        message = f"invalid okf load response: {exc}"
        raise RustCoreError(message) from exc


class RustCoreError(RuntimeError):
    """The optional Rust core failed or returned an invalid response."""


class _FactsPayload(TypedDict):
    links: list[str]
    headings: list[tuple[int, str]]


def _validate_payload(payload: object, expected: int) -> list[_FactsPayload]:
    if not isinstance(payload, list) or len(payload) != expected:
        msg = "response cardinality does not match request"
        raise RustCoreError(msg)
    return cast("list[_FactsPayload]", payload)


def rust_markdown_facts_batch(bodies: Sequence[str], executable: Path) -> tuple[MarkdownFacts, ...]:
    """Extract Markdown facts in one coarse Rust subprocess invocation."""
    completed = subprocess.run(  # noqa: S603
        [executable],
        input=json.dumps({"documents": bodies}, ensure_ascii=False),
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or f"okf-core exited with {completed.returncode}"
        raise RustCoreError(message)
    try:
        payload = _validate_payload(json.loads(completed.stdout), len(bodies))
        return tuple(
            MarkdownFacts(
                links=tuple(item["links"]),
                headings=tuple((int(level), text) for level, text in item["headings"]),
            )
            for item in payload
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        msg = f"invalid okf-core response: {exc}"
        raise RustCoreError(msg) from exc
