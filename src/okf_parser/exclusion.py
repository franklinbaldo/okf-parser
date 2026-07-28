"""Exclude subpaths so a mixed repository can be validated from its real root.

A repository that keeps OKF knowledge next to code, a README and vendored
dependencies has no root that validates cleanly: the root reports ``OKF001``
for every unrelated Markdown file, and checking each bundle separately makes
every cross-bundle link unresolvable. Excluding subpaths lets one root cover
the whole tree, which is the only arrangement under which link validation
actually runs.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

EXCLUSION_FILENAME = ".okfignore"
_COMMENT_PREFIX = "#"
_RECURSIVE_SEGMENT = "**"
_SEPARATOR = "/"
# Compiled patterns are cached across bundles, but the cache is bounded: the
# MCP tools accept patterns from a client, and an unbounded cache would let a
# long-running server grow with every distinct pattern it is ever sent.
_PATTERN_CACHE_SIZE = 512


class ExclusionFileError(ValueError):
    """Raised when an exclusion file exists but cannot be read."""

    def __init__(self, path: Path, reason: str) -> None:
        """Record which file failed and why, so a caller can report both."""
        self.path = path
        self.reason = reason
        super().__init__(f"cannot read exclusion file {path}: {reason}")


def _segment_expression(segment: str) -> str:
    """Translate one glob segment, keeping ``*`` and ``?`` inside the segment."""
    expression = ""
    for character in segment:
        if character == "*":
            expression += "[^/]*"
        elif character == "?":
            expression += "[^/]"
        else:
            expression += re.escape(character)
    return expression


def _normalize(pattern: str) -> str:
    """Drop the surrounding separators that a ``.gitignore`` habit adds.

    Patterns are already anchored at the bundle root, and a match on a
    directory already takes everything below it, so a leading or trailing ``/``
    adds nothing. Left in place it would make ``vendor/`` and ``/vendor``
    compile to expressions that match no path at all — an exclusion that
    silently excludes nothing, which is the failure this module exists to
    prevent.
    """
    return pattern.strip(_SEPARATOR)


@lru_cache(maxsize=_PATTERN_CACHE_SIZE)
def _compile(pattern: str) -> re.Pattern[str]:
    """Compile a normalized, anchored glob pattern into a regular expression.

    ``**`` spans whole segments, everything else stays within one. Anchoring at
    the bundle root is what makes ``*.md`` mean "the Markdown beside the root"
    rather than every document in the tree.
    """
    expression = ""
    segments = pattern.split(_SEPARATOR)
    for index, segment in enumerate(segments):
        if segment == _RECURSIVE_SEGMENT:
            # Absorb the following separator so `**/vendor` also matches
            # `vendor`, the zero-directory case.
            expression += "(?:[^/]+/)*" if index < len(segments) - 1 else ".*"
            continue
        expression += _segment_expression(segment)
        if index < len(segments) - 1:
            expression += _SEPARATOR
    return re.compile(expression)


def _iter_ancestors(relative: str) -> Iterable[str]:
    """Yield the path itself, then each directory above it."""
    segments = relative.split(_SEPARATOR)
    for count in range(len(segments), 0, -1):
        yield _SEPARATOR.join(segments[:count])


class ExclusionRules(BaseModel):
    """Glob patterns that keep a subpath out of a bundle.

    Patterns are anchored at the bundle root and matched against POSIX-style
    relative paths. This is deliberately narrower than ``.gitignore``: there is
    no negation and no implicit "match at any depth", because a pattern that
    silently widened its own scope would drop documents the author meant to
    validate. A leading or trailing ``/`` is accepted and carries no meaning,
    so the ``vendor/`` a ``.gitignore`` habit produces excludes what it looks
    like it excludes.
    """

    model_config = ConfigDict(frozen=True)

    patterns: tuple[str, ...]

    @classmethod
    def read(cls, root: Path, extra: Iterable[str] = ()) -> ExclusionRules:
        """Read the bundle's exclusion file, appending caller-supplied patterns.

        An absent file is the ordinary case and yields no patterns; a file that
        exists but cannot be decoded is an error, because silently ignoring it
        would validate a tree the author believed was filtered.
        """
        if isinstance(extra, str):
            # A bare string is iterable, so it would otherwise become one
            # single-character pattern per letter and quietly exclude nothing.
            msg = f"exclude takes a sequence of patterns, not the single string {extra!r}"
            raise TypeError(msg)
        path = root / EXCLUSION_FILENAME
        patterns: list[str] = []
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                raise ExclusionFileError(path, str(exc)) from exc
            patterns.extend(
                stripped
                for line in text.splitlines()
                if (stripped := line.strip()) and not stripped.startswith(_COMMENT_PREFIX)
            )
        patterns.extend(extra)
        return cls(patterns=tuple(patterns))

    def excludes(self, relative: str) -> bool:
        """Whether a relative POSIX path, or any directory above it, is excluded."""
        expressions = [
            _compile(normalized) for pattern in self.patterns if (normalized := _normalize(pattern))
        ]
        if not expressions:
            return False
        return any(
            expression.fullmatch(ancestor)
            for ancestor in _iter_ancestors(relative)
            for expression in expressions
        )
