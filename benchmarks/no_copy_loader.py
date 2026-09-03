# /// script
# requires-python = ">=3.12"
# ///
"""Same-host end-to-end benchmark for the Rust no-copy loader path."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_FRONTMATTER = (
    "type: Benchmark\n"
    "title: Concept {index:08d}\n"
    "description: Synthetic no-copy benchmark concept\n"
    "ordinal: {index:08d}\n"
)


def _write_bundle(root: Path, documents: int, body_bytes: int) -> None:
    root.mkdir(parents=True)
    payload = "x" * body_bytes
    for index in range(documents):
        source = f"---\n{_FRONTMATTER.format(index=index)}---\n# Concept {index}\n\n{payload}\n"
        (root / f"concept-{index:08d}.md").write_text(source, encoding="utf-8")


def _load(binary: Path, root: Path) -> bytes:
    completed = subprocess.run(  # noqa: S603 -- benchmark executes explicit binary arguments.
        [str(binary), "__engine-load", str(root), "--read-concurrency", "32"],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _samples(operation: Callable[[], object], *, rounds: int, warmups: int) -> list[int]:
    for _ in range(warmups):
        operation()
    samples: list[int] = []
    for _ in range(rounds):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return samples


def _median_ns(operation: Callable[[], object], *, rounds: int, warmups: int) -> int:
    return int(statistics.median(_samples(operation, rounds=rounds, warmups=warmups)))


def _measure(
    baseline_binary: Path,
    candidate_binary: Path,
    case: tuple[int, int],
    timing: tuple[int, int],
) -> dict[str, object]:
    documents, body_bytes = case
    rounds, warmups = timing
    with tempfile.TemporaryDirectory(prefix="okf-no-copy-") as directory:
        root = Path(directory) / "bundle"
        _write_bundle(root, documents, body_bytes)

        baseline_output = _load(baseline_binary, root)
        candidate_output = _load(candidate_binary, root)
        if baseline_output != candidate_output:
            msg = "candidate loader output differs byte-for-byte from the baseline"
            raise RuntimeError(msg)

        baseline_ns = _median_ns(
            lambda: _load(baseline_binary, root), rounds=rounds, warmups=warmups
        )
        candidate_ns = _median_ns(
            lambda: _load(candidate_binary, root), rounds=rounds, warmups=warmups
        )
        return {
            "documents": documents,
            "body_bytes_per_document": body_bytes,
            "baseline_ns": baseline_ns,
            "candidate_ns": candidate_ns,
            "baseline_ns_per_document": baseline_ns // documents,
            "candidate_ns_per_document": candidate_ns // documents,
            "base_to_candidate_speedup": baseline_ns / candidate_ns,
            "latency_reduction": 1 - (candidate_ns / baseline_ns),
            "output_bytes": len(candidate_output),
        }


def _case(value: str) -> tuple[int, int]:
    documents_text, separator, body_bytes_text = value.partition(":")
    if not separator:
        msg = "cases must use DOCUMENTS:BODY_BYTES"
        raise argparse.ArgumentTypeError(msg)
    documents = int(documents_text)
    body_bytes = int(body_bytes_text)
    if documents < 1 or body_bytes < 0:
        msg = "case values must be non-negative, with at least one document"
        raise argparse.ArgumentTypeError(msg)
    return documents, body_bytes


def main() -> None:
    """Run the benchmark matrix and print one machine-readable report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-rust-core", type=Path, required=True)
    parser.add_argument("--candidate-rust-core", type=Path, required=True)
    parser.add_argument(
        "--case",
        action="append",
        type=_case,
        dest="cases",
        default=[],
        metavar="DOCUMENTS:BODY_BYTES",
    )
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    args = parser.parse_args()

    cases = args.cases or [(50_000, 64), (10_000, 1024), (1000, 16_384)]
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    if args.warmups < 0:
        parser.error("--warmups must be non-negative")
    for name in ("baseline_rust_core", "candidate_rust_core"):
        if not getattr(args, name).is_file():
            parser.error(f"--{name.replace('_', '-')} must name an existing executable")

    report = {
        "benchmark": "no-copy-loader-v1",
        "rounds": args.rounds,
        "warmups": args.warmups,
        "cases": [
            _measure(
                args.baseline_rust_core,
                args.candidate_rust_core,
                case,
                (args.rounds, args.warmups),
            )
            for case in cases
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))  # noqa: T201


if __name__ == "__main__":
    main()
