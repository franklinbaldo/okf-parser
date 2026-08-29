"""Shared Markdown body-line semantics.

RFC 0016 makes the existing Python ``str.splitlines()`` behavior observable
across search and relational materialization. Keep this helper deliberately
small: changing its behavior is a conformance change, not an implementation
detail.
"""

from __future__ import annotations


def body_lines(body: str) -> list[str]:
    """Return the logical body lines using the established parser semantics."""
    return body.splitlines()
