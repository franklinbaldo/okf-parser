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
from functools import cache
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

EXCLUSION_FILENAME = ".okfignore"
_COMMENT_PREFIX = "#"
_RECURSIVE_SEGMENT = "**"


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


@cache
def _compile(pattern: str) -> re.Pattern[str]:
    """Compile an anchored glob pattern into a regular expression.

    ``**`` spans whole segments, everything else stays within one. Anchoring at
    the bundle root is what makes ``*.md`` mean "the Markdown beside the root"
    rather than every document in the tree.
    """
    expression = ""
    segments = pattern.split("/")
    for index, segment in enumerate(segments):
        if segment == _RECURSIVE_SEGMENT:
            # Absorb the following separator so `**/vendor` also matches
            # `vendor`, the zero-directory case.
            expression += "(?:[^/]+/)*" if index < len(segments) - 1 else ".*"
            continue
        expression += _segment_expression(segment)
        if index < len(segments) - 1:
            expression += "/"
    return re.compile(expression)


def _iter_ancestors(relative: str) -> Iterable[str]:
    """Yield the path itself, then each directory above it."""
    segments = relative.split("/")
    for count in range(len(segments), 0, -1):
        yield "/".join(segments[:count])


class ExclusionRules(BaseModel):
    """Glob patterns that keep a subpath out of a bundle.

    Patterns are anchored at the bundle root and matched against POSIX-style
    relative paths. This is deliberately narrower than ``.gitignore``: there is
    no negation and no implicit "match at any depth", because a pattern that
    silently widened its own scope would drop documents the author meant to
    validate.
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
        if not self.patterns:
            return False
        expressions = [_compile(pattern) for pattern in self.patterns]
        return any(
            expression.fullmatch(ancestor)
            for ancestor in _iter_ancestors(relative)
            for expression in expressions
        )
