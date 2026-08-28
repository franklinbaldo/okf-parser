# /// script
# requires-python = ">=3.12"
# ///
"""End-to-end benchmark for canonical ordered frontmatter versus the base YAML engine."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

_CANONICAL_TEMPLATE = (
    "type: Benchmark\n"
    "title: Concept {index:08d}\n"
    "description: Synthetic benchmark concept\n"
    "active: true\n"
    "created: 2026-08-28\n"
    "ordinal: {index:08d}\n"
    "status: active\n"
)
_SHUFFLED_TEMPLATE = (
    "status: active\n"
    "ordinal: {index:08d}\n"
    "type: Benchmark\n"
    "active: true\n"
    "title: Concept {index:08d}\n"
    "created: 2026-08-28\n"
    "description: Synthetic benchmark concept\n"
)


def _write_bundle(root: Path, size: int, *, canonical: bool) -> None:
    root.mkdir(parents=True)
    template = _CANONICAL_TEMPLATE if canonical else _SHUFFLED_TEMPLATE
    for index in range(size):
        source = f"---\n{template.format(index=index)}---\n# Concept {index}\n"
        (root / f"concept-{index:08d}.md").write_text(source, encoding="utf-8")


def _load(binary: Path, root: Path) -> bytes:
    completed = subprocess.run(  # noqa: S603 -- benchmark executes explicit binary arguments.
        [str(binary), "__engine-load", str(root), "--read-concurrency", "32"],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _semantic_payload(raw: bytes) -> dict[str, object]:
    payload = cast("dict[str, object]", json.loads(raw))
    payload.pop("root", None)
    concepts = cast("list[dict[str, object]]", payload["concepts"])
    for concept in concepts:
        concept.pop("source_digest", None)
    return payload


def _samples(operation: Callable[[], object], *, rounds: int, warmups: int) -> list[int]:
    for _ in range(warmups):
        operation()
    result: list[int] = []
    for _ in range(rounds):
        started = time.perf_counter_ns()
        operation()
        result.append(time.perf_counter_ns() - started)
    return result


def _median_ns(operation: Callable[[], object], *, rounds: int, warmups: int) -> int:
    return int(statistics.median(_samples(operation, rounds=rounds, warmups=warmups)))


def _measure(
    baseline_binary: Path,
    candidate_binary: Path,
    size: int,
    *,
    rounds: int,
    warmups: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"okf-frontmatter-{size}-") as directory:
        root = Path(directory)
        canonical = root / "canonical"
        shuffled = root / "shuffled"
        _write_bundle(canonical, size, canonical=True)
        _write_bundle(shuffled, size, canonical=False)

        baseline_output = _load(baseline_binary, canonical)
        candidate_output = _load(candidate_binary, canonical)
        fallback_output = _load(candidate_binary, shuffled)
        if _semantic_payload(baseline_output) != _semantic_payload(candidate_output):
            msg = "candidate canonical fast path differs from the base engine"
            raise RuntimeError(msg)
        if _semantic_payload(candidate_output) != _semantic_payload(fallback_output):
            msg = "candidate canonical fast path and YAML fallback differ semantically"
            raise RuntimeError(msg)

        baseline_ns = _median_ns(
            lambda: _load(baseline_binary, canonical), rounds=rounds, warmups=warmups
        )
        candidate_ns = _median_ns(
            lambda: _load(candidate_binary, canonical), rounds=rounds, warmups=warmups
        )
        fallback_ns = _median_ns(
            lambda: _load(candidate_binary, shuffled), rounds=rounds, warmups=warmups
        )
        return {
            "documents": size,
            "baseline_ns": baseline_ns,
            "candidate_ns": candidate_ns,
            "candidate_fallback_ns": fallback_ns,
            "baseline_ns_per_document": baseline_ns // size,
            "candidate_ns_per_document": candidate_ns // size,
            "candidate_fallback_ns_per_document": fallback_ns // size,
            "base_to_candidate_speedup": baseline_ns / candidate_ns,
            "fallback_to_candidate_speedup": fallback_ns / candidate_ns,
            "fallback_vs_base_ratio": fallback_ns / baseline_ns,
            "output_bytes": len(candidate_output),
        }


def main() -> None:
    """Run the benchmark matrix and print one machine-readable report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-rust-core", type=Path, required=True)
    parser.add_argument("--candidate-rust-core", type=Path, required=True)
    parser.add_argument("--sizes", default="1000,10000,50000")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    args = parser.parse_args()

    sizes = [int(value) for value in args.sizes.split(",") if value]
    if not sizes or any(size < 1 for size in sizes):
        parser.error("--sizes must contain positive integers")
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    if args.warmups < 0:
        parser.error("--warmups must be non-negative")
    for name in ("baseline_rust_core", "candidate_rust_core"):
        if not getattr(args, name).is_file():
            parser.error(f"--{name.replace('_', '-')} must name an existing executable")

    report = {
        "benchmark": "ordered-frontmatter-v2",
        "rounds": args.rounds,
        "warmups": args.warmups,
        "sizes": sizes,
        "results": [
            _measure(
                args.baseline_rust_core,
                args.candidate_rust_core,
                size,
                rounds=args.rounds,
                warmups=args.warmups,
            )
            for size in sizes
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))  # noqa: T201


if __name__ == "__main__":
    main()
