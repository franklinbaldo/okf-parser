"""Compare native Python Markdown facts with the experimental Rust batch core."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import TYPE_CHECKING

from okf_parser.parser import markdown_facts
from okf_parser.rust_core import rust_markdown_facts_batch

if TYPE_CHECKING:
    from collections.abc import Callable


def elapsed_ns(operation: Callable[[], object]) -> int:
    """Measure one operation with the monotonic high-resolution clock."""
    start = time.perf_counter_ns()
    operation()
    return time.perf_counter_ns() - start


def main() -> None:
    """Run native and Rust batch facts extraction over one matching corpus."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, default=10_000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--rust-core", type=Path, required=True)
    args = parser.parse_args()
    bodies = [
        f"# Concept {index}\n\n"
        + "\n\n".join(
            f"Paragraph {part} with [next](concept-{(index + 1) % args.documents}.md)."
            for part in range(4)
        )
        + "\n"
        for index in range(args.documents)
    ]

    native = [markdown_facts(body) for body in bodies]
    rust = rust_markdown_facts_batch(bodies, args.rust_core)
    if native != list(rust):
        msg = "native and Rust facts differ"
        raise SystemExit(msg)

    native_ns = statistics.median(
        elapsed_ns(lambda: [markdown_facts(body) for body in bodies]) for _ in range(args.rounds)
    )
    rust_ns = statistics.median(
        elapsed_ns(lambda: rust_markdown_facts_batch(bodies, args.rust_core))
        for _ in range(args.rounds)
    )
    print(  # noqa: T201
        json.dumps(
            {
                "documents": args.documents,
                "rounds": args.rounds,
                "native_ms": native_ns // 1_000_000,
                "rust_batch_ms": rust_ns // 1_000_000,
                "native_ns_per_document": native_ns // args.documents,
                "rust_batch_ns_per_document": rust_ns // args.documents,
                "speedup": native_ns / rust_ns,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
