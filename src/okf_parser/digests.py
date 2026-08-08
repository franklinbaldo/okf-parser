"""Deterministic physical-source and parsed-revision identity for OKF concepts."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from okf_parser.models import YamlValue

_SOURCE_PREFIX = "sha256:"
_REVISION_PREFIX = "okf-revision-v1-sha256:"


def normalize_newlines(text: str) -> str:
    """Normalize CRLF and bare CR to LF for parsed semantic identity."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def source_digest(text: str) -> str:
    """Hash the exact valid UTF-8 text supplied to the parser."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{_SOURCE_PREFIX}{digest}"


def revision_digest(frontmatter: dict[str, YamlValue], body: str) -> str:
    """Hash the versioned canonical parsed representation of one concept."""
    envelope = {
        "body": normalize_newlines(body),
        "frontmatter": frontmatter,
        "version": 1,
    }
    canonical = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_REVISION_PREFIX}{digest}"
