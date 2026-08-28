"""Minimal executable oracle for RFC 0012 impact result conformance."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

_RESULT_PREFIX = "okf-result-v1-sha256:"
_RECORD_KEYS = frozenset({"concept_id", "depth", "support_depths"})
_SUPPORT_KEYS = frozenset({"base", "head"})
_ARG_KEYS = frozenset({"seed", "direction", "max_depth"})
_DIRECTIONS = frozenset({"incoming", "outgoing", "both"})

type _CanonicalRecord = tuple[int, str, int | None, int | None]


def _non_negative_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{label} must be an integer"
        raise TypeError(msg)
    if value < 0:
        msg = f"{label} must be non-negative"
        raise ValueError(msg)
    return value


def _require_string(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        msg = f"{label} must be a string"
        raise TypeError(msg)
    return value


def _require_object(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{label} must be an object"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], *, label: str) -> None:
    keys = set(value)
    missing = sorted(expected - keys)
    unexpected = sorted(keys - expected)
    if missing or unexpected:
        parts = []
        if missing:
            parts.append(f"missing={missing}")
        if unexpected:
            parts.append(f"unexpected={unexpected}")
        msg = f"{label} has invalid keys: {', '.join(parts)}"
        raise ValueError(msg)


def _optional_support_depth(
    support: Mapping[str, object], key: str, *, line_number: int
) -> int | None:
    if key not in support:
        return None
    return _non_negative_integer(
        support[key],
        label=f"{key} depth on line {line_number}",
    )


def _parse_support_depths(value: object, *, line_number: int) -> tuple[int | None, int | None]:
    support = _require_object(value, label=f"support_depths on line {line_number}")
    unexpected = sorted(set(support) - _SUPPORT_KEYS)
    if unexpected:
        msg = f"support_depths on line {line_number} has unexpected keys: {unexpected}"
        raise ValueError(msg)
    if not support:
        msg = f"support_depths on line {line_number} must be non-empty"
        raise ValueError(msg)

    base = _optional_support_depth(support, "base", line_number=line_number)
    head = _optional_support_depth(support, "head", line_number=line_number)
    return base, head


def _parse_impact_record(
    line: str,
    *,
    line_number: int,
    seen_concepts: set[str],
) -> _CanonicalRecord:
    if not line:
        msg = f"impact JSONL line {line_number} must not be blank"
        raise ValueError(msg)

    value = _require_object(json.loads(line), label=f"impact JSONL line {line_number}")
    _exact_keys(value, _RECORD_KEYS, label=f"impact record line {line_number}")

    concept_id = _require_string(value["concept_id"], label=f"concept_id on line {line_number}")
    if concept_id in seen_concepts:
        msg = f"impact result must emit concept_id once: {concept_id!r}"
        raise ValueError(msg)
    seen_concepts.add(concept_id)

    depth = _non_negative_integer(value["depth"], label=f"depth on line {line_number}")
    base, head = _parse_support_depths(value["support_depths"], line_number=line_number)
    minimum = min(item for item in (base, head) if item is not None)
    if depth != minimum:
        msg = f"depth on line {line_number} must equal min(support_depths.values())"
        raise ValueError(msg)

    return depth, concept_id, base, head


def _serialize_impact_record(record: _CanonicalRecord) -> str:
    depth, concept_id, base, head = record
    support: dict[str, int] = {}
    if base is not None:
        support["base"] = base
    if head is not None:
        support["head"] = head
    value = {
        "concept_id": concept_id,
        "depth": depth,
        "support_depths": support,
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonicalize_result_impact(raw_result: str | bytes) -> bytes:
    """Validate and canonically serialize RFC 0012 impact JSONL.

    The v1 oracle intentionally contains only ``concept_id``, ``depth`` and
    ``support_depths``. Physical-plan/debug fields are rejected rather than
    ignored so they cannot silently become semantic output.
    """
    text = raw_result.decode("utf-8") if isinstance(raw_result, bytes) else raw_result
    if text == "":
        return b""

    seen_concepts: set[str] = set()
    records = [
        _parse_impact_record(line, line_number=line_number, seen_concepts=seen_concepts)
        for line_number, line in enumerate(text.splitlines(), start=1)
    ]
    records.sort(key=lambda item: (item[0], item[1]))
    lines = [_serialize_impact_record(record) for record in records]
    return ("\n".join(lines) + "\n").encode()


def canonicalize_impact_args(args: Mapping[str, object]) -> bytes:
    """Serialize only v1 impact arguments that participate in result identity."""
    _exact_keys(args, _ARG_KEYS, label="impact args")

    seed = _require_string(args["seed"], label="impact seed")
    direction = _require_string(args["direction"], label="impact direction")
    if direction not in _DIRECTIONS:
        msg = "impact direction must be one of incoming, outgoing, both"
        raise ValueError(msg)

    max_depth = _non_negative_integer(args["max_depth"], label="impact max_depth")
    canonical = {
        "seed": seed,
        "direction": direction,
        "max_depth": max_depth,
    }
    return json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()


def impact_result_digest(args: Mapping[str, object], raw_result: str | bytes) -> str:
    """Digest the v1 impact question and its canonical observable answer."""
    canonical_args = canonicalize_impact_args(args)
    canonical_result = canonicalize_result_impact(raw_result)

    hasher = hashlib.sha256()
    hasher.update(b"okf-result-v1\0")
    hasher.update(b"impact\0")
    hasher.update(canonical_args)
    hasher.update(b"\0")
    hasher.update(canonical_result)
    return f"{_RESULT_PREFIX}{hasher.hexdigest()}"
