"""Deterministic UTF-8/LF filesystem writes shared by generators."""

from __future__ import annotations

from pathlib import Path

from okf_parser.digests import normalize_newlines


def lf_bytes(text: str) -> bytes:
    """Encode text as UTF-8 after normalizing every newline spelling to LF."""
    return normalize_newlines(text).encode("utf-8")


def atomic_write_lf_text(path: Path, text: str) -> None:
    """Atomically replace one text file using platform-independent LF bytes."""
    staged = path.with_name(f".{path.name}.okf-write.tmp")
    staged.write_bytes(lf_bytes(text))
    staged.replace(path)
