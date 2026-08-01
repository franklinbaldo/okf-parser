"""Exact lexical classification shared by schema inference and explicit casts."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

type CastKind = Literal["string", "boolean", "integer", "number", "date", "datetime"]

_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_NUMBER_RE = re.compile(r"^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DATETIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2})"
    r"(?::(\d{2})(?:\.(\d+))?)?(?:[Zz]|[+-](\d{2}):(\d{2}))?$"
)


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _is_calendar_date(year: int, month: int, day: int) -> bool:
    if year < 1 or month < 1 or month > 12 or day < 1:
        return False
    days = (31, 29 if _is_leap_year(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return day <= days[month - 1]


def is_iso_date_lexeme(value: str) -> bool:
    """Return whether ``value`` is a real calendar date in ``YYYY-MM-DD`` form."""
    match = _DATE_RE.fullmatch(value)
    if match is None:
        return False
    year, month, day = (int(part) for part in match.groups())
    return _is_calendar_date(year, month, day)


def is_iso_datetime_lexeme(value: str) -> bool:
    """Return whether ``value`` is a supported ISO-like local or offset datetime."""
    match = _DATETIME_RE.fullmatch(value)
    if match is None:
        return False
    year, month, day, hour, minute, second, _fraction, offset_hour, offset_minute = (
        match.groups()
    )
    if not _is_calendar_date(int(year), int(month), int(day)):
        return False
    return (
        int(hour) <= 23
        and int(minute) <= 59
        and int(second or "0") <= 59
        and int(offset_hour or "0") <= 23
        and int(offset_minute or "0") <= 59
    )


def classify_lexemes(values: Sequence[str]) -> CastKind:
    """Classify an aggregate only when every observed spelling supports one kind."""
    if not values:
        return "string"
    if all(value.casefold() in {"true", "false"} for value in values):
        return "boolean"
    if all(_INTEGER_RE.fullmatch(value) is not None for value in values):
        return "integer"
    if all(_NUMBER_RE.fullmatch(value) is not None for value in values):
        return "number"
    if all(is_iso_date_lexeme(value) for value in values):
        return "date"
    if all(is_iso_datetime_lexeme(value) for value in values):
        return "datetime"
    return "string"


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
