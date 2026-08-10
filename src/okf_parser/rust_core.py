"""Bridge and deterministic discovery for the optional Rust OKF engine."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast

from okf_parser.parser import MarkdownFacts

if TYPE_CHECKING:
    from collections.abc import Sequence

EngineMode = Literal["auto", "native"]


def _binary_name() -> str:
    return "okf-core.exe" if os.name == "nt" else "okf-core"


def packaged_rust_core() -> Path | None:
    """Return the package-local native engine when this distribution ships one."""
    candidate = Path(__file__).resolve().parent / "_native" / _binary_name()
    return candidate if candidate.is_file() else None


def resolve_rust_core(
    *,
    engine: EngineMode = "auto",
    explicit: Path | None = None,
    environ: dict[str, str] | None = None,
    path_lookup: object = shutil.which,
) -> Path | None:
    """Resolve the best available Rust engine without leaking deployment into callers."""
    if engine not in {"auto", "native"}:
        msg = f"unsupported OKF engine mode: {engine}"
        raise ValueError(msg)
    if engine == "native":
        return None
    if explicit is not None:
        return explicit

    packaged = packaged_rust_core()
    if packaged is not None:
        return packaged

    environment = os.environ if environ is None else environ
    configured = environment.get("OKF_CORE")
    if configured:
        return Path(configured)

    lookup = cast("object", path_lookup)
    resolved = lookup(_binary_name()) if callable(lookup) else None
    return Path(resolved) if isinstance(resolved, str) and resolved else None


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
