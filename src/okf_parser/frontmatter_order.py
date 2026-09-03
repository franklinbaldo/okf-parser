"""Canonical physical ordering for the simple OKF frontmatter fast path.

Field order is deliberately non-semantic.  Readers must accept every YAML
mapping accepted by the normal parser; this module only identifies the small,
common physical form that writers can make predictable enough for a cheaper
native parser.
"""

from __future__ import annotations

import re
from typing import Final

PREFERRED_FRONTMATTER_KEYS: Final = ("type", "title", "description")
"""Human-first prefix of the canonical physical frontmatter order."""

_SIMPLE_KEY: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_UNSAFE_VALUE_PREFIXES: Final = frozenset("-?:,[]{}#&*!|>'\"%@`")


def frontmatter_key_order(key: str) -> tuple[int, str]:
    """Return the non-semantic physical sort key for one top-level field."""
    try:
        return (PREFERRED_FRONTMATTER_KEYS.index(key), "")
    except ValueError:
        return (len(PREFERRED_FRONTMATTER_KEYS), key)


def _simple_line(line: str) -> tuple[str, str] | None:
    """Return ``(key, original_line)`` for the deliberately tiny fast subset."""
    key, separator, remainder = line.partition(":")
    if not separator or _SIMPLE_KEY.fullmatch(key) is None:
        return None
    if remainder:
        if not remainder.startswith(" ") or remainder.startswith("  "):
            return None
        value = remainder[1:]
    else:
        value = ""
    if value != value.strip() or "\t" in value or "#" in value or ": " in value:
        return None
    if value and value[0] in _UNSAFE_VALUE_PREFIXES:
        return None
    return key, line


def canonicalize_simple_frontmatter_block(block: str) -> str:
    """Reorder a flat safe frontmatter block, leaving every other block byte-for-byte.

    The function never rewrites scalar spelling.  If a block contains comments,
    quoting, nesting, flow collections, anchors, tags, multiline scalars,
    duplicate keys, or another form whose YAML meaning is not obvious from one
    physical line, it is returned unchanged for the ordinary YAML parser.
    """
    if not block:
        return block
    terminal_newline = block.endswith("\n")
    content = block[:-1] if terminal_newline else block
    if not content:
        return block

    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in content.split("\n"):
        parsed = _simple_line(line)
        if parsed is None:
            return block
        key, original = parsed
        if key in seen:
            return block
        seen.add(key)
        rows.append((key, original))
    ordered = sorted(rows, key=lambda row: frontmatter_key_order(row[0]))
    result = "\n".join(line for _, line in ordered)
    return result + ("\n" if terminal_newline else "")
