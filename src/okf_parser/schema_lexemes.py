"""Exact lexical classification shared by schema inference and explicit casts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

type CastKind = Literal["string", "boolean", "integer", "number", "date", "datetime"]

_MONTHS_PER_YEAR = 12
_MAX_HOUR = 23
_MAX_MINUTE_OR_SECOND = 59
_INTEGER_RE = re.compile(r"^[+-]?[0-9]+$")
_NUMBER_RE = re.compile(
    r"^[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?$"
)
_DATE_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_DATETIME_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2})"
    r"(?::([0-9]{2})(?:\.([0-9]+))?)?(?:[Zz]|[+-]([0-9]{2}):([0-9]{2}))?$"
)


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _is_calendar_date(year: int, month: int, day: int) -> bool:
    if year < 1 or month < 1 or month > _MONTHS_PER_YEAR or day < 1:
        return False
    days = (31, 29 if _is_leap_year(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return day <= days[month - 1]


def is_iso_date_lexeme(value: str) -> bool:
    """Return whether ``value`` is a real calendar date in ``YYYY-MM-DD`` form."""
    match = _DATE_RE.fullmatch(value)
    if match is None:
        return False
    return _is_calendar_date(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )


def is_iso_datetime_lexeme(value: str) -> bool:
    """Return whether ``value`` is a supported ISO-like local or offset datetime."""
    match = _DATETIME_RE.fullmatch(value)
    if match is None:
        return False
    if not _is_calendar_date(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    ):
        return False
    return (
        int(match.group(4)) <= _MAX_HOUR
        and int(match.group(5)) <= _MAX_MINUTE_OR_SECOND
        and int(match.group(6) or "0") <= _MAX_MINUTE_OR_SECOND
        and int(match.group(8) or "0") <= _MAX_HOUR
        and int(match.group(9) or "0") <= _MAX_MINUTE_OR_SECOND
    )


def classify_lexemes(values: Sequence[str]) -> CastKind:
    """Classify an aggregate only when every observed spelling supports one kind."""
    kind: CastKind = "string"
    if values:
        if all(value.casefold() in {"true", "false"} for value in values):
            kind = "boolean"
        elif all(_INTEGER_RE.fullmatch(value) is not None for value in values):
            kind = "integer"
        elif all(_NUMBER_RE.fullmatch(value) is not None for value in values):
            kind = "number"
        elif all(is_iso_date_lexeme(value) for value in values):
            kind = "date"
        elif all(is_iso_datetime_lexeme(value) for value in values):
            kind = "datetime"
    return kind


def can_classify_as(values: Sequence[str], kind: CastKind) -> bool:
    """Return whether every spelling satisfies an explicit cast without coercion guesses."""
    if not values or kind == "string":
        return True
    inferred = classify_lexemes(values)
    if kind == "number":
        return inferred in {"integer", "number"}
    if kind == "datetime":
        return inferred in {"date", "datetime"}
    return inferred == kind
