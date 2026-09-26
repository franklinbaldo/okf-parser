"""Find the native ``okf-parser`` binary and speak its JSON protocol.

The protocol is UTF-8 on every platform: each call pins ``encoding="utf-8"``
so the process locale (a cp1252 Windows console, say) never decides how
Markdown crosses the pipe.

The binary is the product (RFC 0024): every OKF rule lives there, and this
package is a shell over it. Each response carries a ``protocol`` version that
the models here pin, so a mismatched binary fails loudly instead of being
misread.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from okf_parser.graph import GraphSummary
from okf_parser.models import ConceptRecord, LinkRecord, ReservedRecord, Violation
from okf_parser.parser import MarkdownFacts

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

PROTOCOL_VERSION = 1
"""The shell/binary protocol this package speaks; see ``rust-core/src/protocol.rs``."""


def _binary_name() -> str:
    return "okf-parser.exe" if os.name == "nt" else "okf-parser"


def packaged_rust_core() -> Path | None:
    """Return a native engine installed with this Python environment, when present."""
    package_local = Path(__file__).resolve().parent / "_native" / _binary_name()
    if package_local.is_file():
        return package_local

    scripts = sysconfig.get_path("scripts")
    if scripts:
        companion = Path(scripts) / _binary_name()
        if companion.is_file():
            return companion
    return None


def resolve_rust_core(
    *,
    explicit: Path | None = None,
    environ: dict[str, str] | None = None,
    path_lookup: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """Locate the native binary: explicit, packaged, ``OKF_CORE``, then ``PATH``."""
    if explicit is not None:
        return explicit

    packaged = packaged_rust_core()
    if packaged is not None:
        return packaged

    environment = os.environ if environ is None else environ
    configured = environment.get("OKF_CORE")
    if configured:
        return Path(configured)

    resolved = path_lookup(_binary_name())
    return Path(resolved) if resolved else None


class NativeBinaryMissingError(RuntimeError):
    """No ``okf-parser`` binary could be found; the shell cannot work without it."""

    def __init__(self) -> None:
        """Say where the binary is looked for."""
        super().__init__(
            "the native okf-parser binary was not found: reinstall okf-parser, "
            "or point OKF_CORE at a built binary"
        )


def native_binary(explicit: Path | None = None) -> Path:
    """Return the binary to call, or fail: there is no Python fallback."""
    executable = resolve_rust_core(explicit=explicit)
    if executable is None:
        raise NativeBinaryMissingError
    return executable


class BundleRecords(BaseModel):
    """One bundle's records, diagnostics and graph summary, as the binary loaded them."""

    model_config = ConfigDict(frozen=True)

    root: str
    concepts: tuple[ConceptRecord, ...]
    reserved: tuple[ReservedRecord, ...]
    links: tuple[LinkRecord, ...]
    diagnostics: tuple[Violation, ...]
    markdown_count: int
    graph: GraphSummary


class EngineLoad(BundleRecords):
    """``__engine-load``: a bundle's records under protocol 1."""

    protocol: Literal[1]


def rust_load_bundle(
    root: Path, executable: Path, exclude: Sequence[str] = (), *, read_concurrency: int = 32
) -> EngineLoad:
    """Run the native engine and validate its answer against the protocol."""
    command = [
        str(executable),
        "__engine-load",
        str(root),
        "--read-concurrency",
        str(read_concurrency),
    ]
    for pattern in exclude:
        command.extend(("--exclude", pattern))
    completed = subprocess.run(  # noqa: S603
        command, capture_output=True, check=False, encoding="utf-8", errors="strict"
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or f"okf exited with {completed.returncode}"
        raise RustCoreError(message)
    try:
        return EngineLoad.model_validate_json(completed.stdout)
    except ValidationError as exc:
        message = f"invalid okf load response (protocol {PROTOCOL_VERSION} expected): {exc}"
        raise RustCoreError(message) from exc


class NativeError(BaseModel):
    """Why the binary could not answer a command.

    ``request`` means the request is invalid for this bundle (the caller's
    fault); ``spec_template`` is the request error of a specification
    template without ``{slug}``; ``io`` means the filesystem failed
    underneath the command.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["request", "spec_template", "io"]
    message: str


class NativeResponse(BaseModel):
    """A command's answer under protocol 1: exactly one of ``result`` or ``error``."""

    model_config = ConfigDict(frozen=True)

    protocol: Literal[1]
    result: dict[str, JsonValue] | None = None
    error: NativeError | None = None


def call_native(command: str, request: BaseModel, executable: Path | None = None) -> NativeResponse:
    """Send one JSON request to a hidden binary command and validate the answer."""
    completed = subprocess.run(  # noqa: S603 - fixed argv to the packaged binary
        [str(native_binary(executable)), command],
        input=request.model_dump_json(),
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="strict",
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or f"okf-parser {command} exited {completed.returncode}"
        raise RustCoreError(message)
    try:
        return NativeResponse.model_validate_json(completed.stdout)
    except ValidationError as exc:
        message = (
            f"invalid okf-parser {command} response (protocol {PROTOCOL_VERSION} expected): {exc}"
        )
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
        [executable, "__engine-facts"],
        input=json.dumps({"documents": bodies}, ensure_ascii=False),
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="strict",
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
