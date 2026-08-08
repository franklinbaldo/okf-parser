"""Reproducible parsing benchmarks for the Python runtime."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from okf_parser.bundle import load_bundle
from okf_parser.parser import (
    _split_frontmatter_source,
    iter_headings,
    iter_markdown_links,
    parse_document_text,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _document(index: int, body_paragraphs: int) -> str:
    body = "\n\n".join(
        f"Paragraph {part} with [next](concept-{index + 1}.md)." for part in range(body_paragraphs)
    )
    return (
        "---\n"
        "type: Benchmark\n"
        f"title: Concept {index}\n"
        f"ordinal: {index:06d}\n"
        "---\n"
        f"# Concept {index}\n\n{body}\n"
    )


def _median_ns(operation: Callable[[], None], rounds: int) -> int:
    operation()
    samples: list[int] = []
    for _ in range(rounds):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return int(statistics.median(samples))


def _write_bundle(root: Path, documents: list[str]) -> None:
    (root / "index.md").write_text("# Benchmark bundle\n", encoding="utf-8")
    for index, source in enumerate(documents):
        (root / f"concept-{index}.md").write_text(source, encoding="utf-8")


def _measure(size: int, body_paragraphs: int, rounds: int) -> dict[str, int]:
    documents = [_document(index, body_paragraphs) for index in range(size)]
    bodies = [source.split("---\n", 2)[2] for source in documents]

    split_ns = _median_ns(
        lambda: [_split_frontmatter_source(source) for source in documents],
        rounds,
    )
    parse_ns = _median_ns(
        lambda: [
            parse_document_text(Path(f"concept-{index}.md"), source)
            for index, source in enumerate(documents)
        ],
        rounds,
    )
    links_ns = _median_ns(lambda: [iter_markdown_links(body) for body in bodies], rounds)
    headings_ns = _median_ns(lambda: [iter_headings(body) for body in bodies], rounds)

    with tempfile.TemporaryDirectory(prefix="okf-parser-benchmark-") as directory:
        root = Path(directory)
        _write_bundle(root, documents)
        load_ns = _median_ns(lambda: load_bundle(root), rounds)

    return {
        "documents": size,
        "source_bytes": sum(len(source.encode()) for source in documents),
        "frontmatter_split_ns_per_document": split_ns // size,
        "document_parse_ns_per_document": parse_ns // size,
        "markdown_links_ns_per_document": links_ns // size,
        "markdown_headings_ns_per_document": headings_ns // size,
        "bundle_load_ns_per_document": load_ns // size,
        "bundle_load_ms": load_ns // 1_000_000,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="100,1000,5000")
    parser.add_argument("--body-paragraphs", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    sizes = [int(value) for value in args.sizes.split(",") if value]

    report = {
        "runtime": "python",
        "runtime_version": platform.python_version(),
        "rounds": args.rounds,
        "body_paragraphs": args.body_paragraphs,
        "results": [_measure(size, args.body_paragraphs, args.rounds) for size in sizes],
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
