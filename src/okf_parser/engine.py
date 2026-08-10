"""Application-facing bundle loader with automatic engine selection."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from okf_parser.bundle import Bundle, load_bundle as _load_bundle_native
from okf_parser.rust_core import EngineMode, resolve_rust_core

if TYPE_CHECKING:
    from collections.abc import Sequence


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
