"""Application-facing bundle loader with automatic engine selection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.bundle import load_bundle as _load_bundle_native
from okf_parser.bundle import validate_path as _validate_path_native
from okf_parser.rust_core import resolve_rust_core

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from okf_parser.bundle import Bundle
    from okf_parser.models import ValidationReport
    from okf_parser.rust_core import EngineMode


def load_bundle(
    root: Path,
    exclude: Sequence[str] = (),
    *,
    engine: EngineMode = "auto",
    rust_core: Path | None = None,
) -> Bundle:
    """Load a bundle with the best available compatible engine."""
    executable = resolve_rust_core(engine=engine, explicit=rust_core)
    return _load_bundle_native(root, exclude, rust_core=executable)


def validate_path(
    path: Path,
    exclude: Sequence[str] = (),
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
    relational_schema: Path | None = None,
    engine: EngineMode = "auto",
    rust_core: Path | None = None,
) -> ValidationReport:
    """Validate a bundle with the best available compatible engine."""
    executable = resolve_rust_core(engine=engine, explicit=rust_core)
    return _validate_path_native(
        path,
        exclude,
        require_spec,
        normative_spec=normative_spec,
        relational_schema=relational_schema,
        rust_core=executable,
    )
