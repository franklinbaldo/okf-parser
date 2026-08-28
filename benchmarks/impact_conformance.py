# /// script
# requires-python = ">=3.12"
# ///
"""Microbenchmark the executable RFC 0012 impact conformance kernel."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import runpy
import statistics
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

_ROOT = Path(__file__).resolve().parents[1]
_KERNEL = runpy.run_path(str(_ROOT / "tests" / "conformance_kernel.py"))

type _CanonicalRecord = tuple[int, str, int | None, int | None]
type _ParseRecord = Callable[..., _CanonicalRecord]
type _SerializeRecord = Callable[[_CanonicalRecord], str]
type _CanonicalizeResult = Callable[[str | bytes], bytes]
type _CanonicalizeArgs = Callable[[Mapping[str, object]], bytes]
type _ResultDigest = Callable[[Mapping[str, object], str | bytes], str]

_PARSE_RECORD = cast("_ParseRecord", _KERNEL["_parse_impact_record"])
_SERIALIZE_RECORD = cast("_SerializeRecord", _KERNEL["_serialize_impact_record"])
_CANONICALIZE_RESULT = cast("_CanonicalizeResult", _KERNEL["canonicalize_result_impact"])
_CANONICALIZE_ARGS = cast("_CanonicalizeArgs", _KERNEL["canonicalize_impact_args"])
_RESULT_DIGEST = cast("_ResultDigest", _KERNEL["impact_result_digest"])

_ARGS: dict[str, object] = {
    "seed": "concept-00000000",
    "direction": "both",
    "max_depth": 8,
}


def _raw_result(size: int) -> str:
    records: list[str] = []
    for index in reversed(range(size)):
        depth = index % 9
        support: dict[str, int] = {}
        match index % 3:
            case 0:
                support["head"] = depth + 2
                support["base"] = depth
            case 1:
                support["base"] = depth
            case _:
                support["head"] = depth
        value = {
            "support_depths": support,
            "depth": depth,
            "concept_id": f"concept-{index:08d}",
        }
        records.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(records) + ("\n" if records else "")


def _parse_validate(lines: list[str]) -> list[_CanonicalRecord]:
    seen: set[str] = set()
    return [
        _PARSE_RECORD(line, line_number=line_number, seen_concepts=seen)
        for line_number, line in enumerate(lines, start=1)
    ]


def _normalize(records: list[_CanonicalRecord]) -> list[_CanonicalRecord]:
    return sorted(records, key=lambda item: (item[0], item[1]))


def _serialize(records: list[_CanonicalRecord]) -> bytes:
    if not records:
        return b""
    return ("\n".join(_SERIALIZE_RECORD(record) for record in records) + "\n").encode()


def _hash_only(canonical_args: bytes, canonical_result: bytes) -> bytes:
    hasher = hashlib.sha256()
    hasher.update(b"okf-result-v1\0")
    hasher.update(b"impact\0")
    hasher.update(canonical_args)
    hasher.update(b"\0")
    hasher.update(canonical_result)
    return hasher.digest()


def _samples_ns(operation: Callable[[], object], *, rounds: int, warmups: int) -> list[int]:
    for _ in range(warmups):
        operation()
    samples: list[int] = []
    for _ in range(rounds):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return samples


def _summary(samples: list[int], *, records: int) -> dict[str, int]:
    ordered = sorted(samples)
    p95_index = max(0, (95 * len(ordered) + 99) // 100 - 1)
    median_ns = int(statistics.median(samples))
    return {
        "median_ns": median_ns,
        "p95_ns": ordered[p95_index],
        "median_ns_per_record": median_ns // max(records, 1),
    }


def _measure(size: int, *, rounds: int, warmups: int) -> dict[str, object]:
    raw_result = _raw_result(size)
    lines = raw_result.splitlines()
    parsed = _parse_validate(lines)
    normalized = _normalize(parsed)
    canonical_result = _serialize(normalized)
    canonical_args = _CANONICALIZE_ARGS(_ARGS)

    if _CANONICALIZE_RESULT(raw_result) != canonical_result:
        msg = "phase composition differs from canonicalize_result_impact"
        raise RuntimeError(msg)
    if _CANONICALIZE_RESULT(canonical_result) != canonical_result:
        msg = "canonicalize_result_impact is not idempotent"
        raise RuntimeError(msg)

    operations: dict[str, Callable[[], object]] = {
        "parse_validate": lambda: _parse_validate(lines),
        "normalize_sort": lambda: _normalize(parsed),
        "serialize": lambda: _serialize(normalized),
        "hash_only": lambda: _hash_only(canonical_args, canonical_result),
        "canonicalize_result": lambda: _CANONICALIZE_RESULT(raw_result),
        "result_digest": lambda: _RESULT_DIGEST(_ARGS, raw_result),
    }
    return {
        "records": size,
        "raw_bytes": len(raw_result.encode()),
        "canonical_bytes": len(canonical_result),
        "operations": {
            name: _summary(
                _samples_ns(operation, rounds=rounds, warmups=warmups),
                records=size,
            )
            for name, operation in operations.items()
        },
    }


def main() -> None:
    """Run the selected matrix and emit a machine-readable JSON report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="1,10,100,1000,10000,50000")
    parser.add_argument("--rounds", type=int, default=11)
    parser.add_argument("--warmups", type=int, default=2)
    args = parser.parse_args()

    sizes = [int(value) for value in args.sizes.split(",") if value]
    if not sizes or any(size < 0 for size in sizes):
        parser.error("--sizes must contain non-negative integers")
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    if args.warmups < 0:
        parser.error("--warmups must be non-negative")

    report = {
        "benchmark": "impact-conformance-v1",
        "runtime": "python",
        "runtime_version": platform.python_version(),
        "rounds": args.rounds,
        "warmups": args.warmups,
        "sizes": sizes,
        "results": [_measure(size, rounds=args.rounds, warmups=args.warmups) for size in sizes],
    }
    print(json.dumps(report, indent=2, sort_keys=True))  # noqa: T201


if __name__ == "__main__":
    main()
