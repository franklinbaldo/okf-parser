"""Exclude subpaths so a mixed repository can be validated from its real root.

A repository that keeps OKF knowledge next to code, a README and vendored
dependencies has no root that validates cleanly: the root reports ``OKF001``
for every unrelated Markdown file, and checking each bundle separately makes
every cross-bundle link unresolvable. Excluding subpaths lets one root cover
the whole tree, which is the only arrangement under which link validation
actually runs.

``.okfignore`` uses ``.gitignore`` pattern semantics. Wearing that filename
while matching by different rules was a trap: a monorepo could not express
"exclude ``vendor`` except ``vendor/knowledge``" at all, and every author had
to learn which half of their habit applied. One deviation remains, and it
widens what an author can say rather than narrowing it — see ``ExclusionRules``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

EXCLUSION_FILENAME = ".okfignore"
_COMMENT_PREFIX = "#"
_NEGATION_PREFIX = "!"
_ESCAPE = "\\"
_SEPARATOR = "/"
_RECURSIVE_SEGMENT = "**"
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


@dataclass(frozen=True, slots=True)
class _Rule:
    """One compiled ``.okfignore`` line."""

    expression: re.Pattern[str]
    negated: bool
    directory_only: bool

    def matches(self, relative: str, *, is_dir: bool) -> bool:
        """Whether this rule decides the fate of one relative path."""
        if self.directory_only and not is_dir:
            return False
        return self.expression.fullmatch(relative) is not None


def _strip_trailing_space(pattern: str) -> str:
    """Drop unescaped trailing spaces, which ``.gitignore`` treats as noise."""
    end = len(pattern)
    while end > 0 and pattern[end - 1] == " ":
        escapes = 0
        while end - 2 - escapes >= 0 and pattern[end - 2 - escapes] == _ESCAPE:
            escapes += 1
        if escapes % 2:
            break
        end -= 1
    return pattern[:end]


def _class_expression(pattern: str, index: int) -> tuple[str, int] | None:
    """Translate a ``[...]`` character class, returning it and the next index."""
    end = index + 1
    if end < len(pattern) and pattern[end] in {_NEGATION_PREFIX, "^"}:
        end += 1
    if end < len(pattern) and pattern[end] == "]":
        end += 1
    while end < len(pattern) and pattern[end] != "]":
        end += 1
    if end >= len(pattern):
        return None
    body = pattern[index + 1 : end]
    if body.startswith(_NEGATION_PREFIX):
        body = "^" + body[1:]
    return f"[{body}]", end + 1


def _segment_expression(segment: str) -> str:
    """Translate one glob segment, keeping ``*``, ``?`` and classes inside it."""
    expression = ""
    index = 0
    while index < len(segment):
        character = segment[index]
        if character == _ESCAPE and index + 1 < len(segment):
            expression += re.escape(segment[index + 1])
            index += 2
            continue
        if character == "*":
            expression += "[^/]*"
        elif character == "?":
            expression += "[^/]"
        elif character == "[" and (translated := _class_expression(segment, index)) is not None:
            expression += translated[0]
            index = translated[1]
            continue
        else:
            expression += re.escape(character)
        index += 1
    return expression


def _body_expression(pattern: str) -> str:
    """Translate a separator-anchored pattern body into a regular expression."""
    expression = ""
    segments = pattern.split(_SEPARATOR)
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == _RECURSIVE_SEGMENT:
            # Absorb the following separator so `**/vendor` also matches
            # `vendor`, the zero-directory case.
            expression += ".*" if last else "(?:[^/]+/)*"
            continue
        expression += _segment_expression(segment)
        if not last:
            expression += _SEPARATOR
    return expression


@lru_cache(maxsize=_PATTERN_CACHE_SIZE)
def _compile(pattern: str) -> _Rule | None:
    """Compile one ``.okfignore`` line, or ``None`` when it declares nothing."""
    line = _strip_trailing_space(pattern)
    if not line or line.startswith(_COMMENT_PREFIX):
        return None
    negated = line.startswith(_NEGATION_PREFIX)
    # A leading `\` escapes the `!` or `#` that would otherwise be syntax.
    if negated or (line.startswith(_ESCAPE) and line[1:2] in {_COMMENT_PREFIX, _NEGATION_PREFIX}):
        line = line[1:]
    directory_only = line.endswith(_SEPARATOR) and not line.endswith(_ESCAPE + _SEPARATOR)
    if directory_only:
        line = line[:-1]
    if not line:
        return None
    # A separator anywhere but at the end anchors the pattern at the bundle
    # root; without one the pattern matches its name at any depth.
    anchored = _SEPARATOR in line
    body = _body_expression(line.removeprefix(_SEPARATOR))
    if not anchored:
        body = f"(?:[^/]+/)*{body}"
    return _Rule(expression=re.compile(body), negated=negated, directory_only=directory_only)


def _iter_ancestors(relative: str) -> Iterable[tuple[str, bool]]:
    """Yield each directory above a path, then the path itself."""
    segments = relative.split(_SEPARATOR)
    for count in range(1, len(segments) + 1):
        yield _SEPARATOR.join(segments[:count]), count < len(segments)


class ExclusionRules(BaseModel):
    """``.gitignore`` patterns that keep a subpath out of a bundle.

    Patterns follow ``.gitignore``: a pattern without a separator matches its
    name at any depth, a separator anchors it at the bundle root, a trailing
    ``/`` matches directories only, ``!`` re-includes, and the last pattern
    that matches a path decides its fate.

    One deviation, and it is deliberate: ``.gitignore`` cannot re-include a
    path whose parent directory is excluded, because git prunes the walk. Here
    ``vendor`` followed by ``!vendor/knowledge`` does re-include, since a
    negation that silently does nothing is exactly the surprise this module
    exists to prevent. Without any negation the walk still prunes, so the fast
    path over a vendored dependency of several hundred documents is unchanged.
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
                line for raw in text.split("\n") if _compile(line := raw.rstrip("\r")) is not None
            )
        patterns.extend(extra)
        return cls(patterns=tuple(patterns))

    @property
    def rules(self) -> tuple[_Rule, ...]:
        """Every line that declares a rule, in the order the author wrote it."""
        return tuple(rule for pattern in self.patterns if (rule := _compile(pattern)) is not None)

    @property
    def has_negation(self) -> bool:
        """Whether any rule re-includes, which is what forbids pruning a walk."""
        return any(rule.negated for rule in self.rules)

    def excludes(self, relative: str, *, is_dir: bool = False) -> bool:
        """Whether a relative POSIX path is excluded once every rule is applied.

        A path with no rule of its own inherits the decision of the nearest
        directory above it, so excluding a directory still takes its whole
        subtree without one pattern per document.
        """
        rules = self.rules
        if not rules:
            return False
        excluded = False
        for ancestor, ancestor_is_dir in _iter_ancestors(relative):
            decisive = [
                rule for rule in rules if rule.matches(ancestor, is_dir=ancestor_is_dir or is_dir)
            ]
            if decisive:
                excluded = not decisive[-1].negated
        return excluded
