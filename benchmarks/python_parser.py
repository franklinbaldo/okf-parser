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
    iter_headings,
    iter_markdown_links,
    parse_document_text,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _is_delimiter(line: str) -> bool:
    content = line.removesuffix("\n").removesuffix("\r")
    return content.startswith("---") and not content.removeprefix("---").strip(" \t")


def _split_frontmatter_source(text: str) -> tuple[str, str] | None:
    normalized = text.removeprefix("\ufeff")
    opening_end = normalized.find("\n")
    if opening_end < 0 or not _is_delimiter(normalized[: opening_end + 1]):
        return None

    cursor = opening_end + 1
    while cursor <= len(normalized):
        newline = normalized.find("\n", cursor)
        line_end = len(normalized) if newline < 0 else newline + 1
        if _is_delimiter(normalized[cursor:line_end]):
            block_end = cursor
            if block_end > opening_end + 1 and normalized[block_end - 1] == "\n":
                block_end -= 1
                if block_end > opening_end + 1 and normalized[block_end - 1] == "\r":
                    block_end -= 1
            return normalized[opening_end + 1 : block_end], normalized[line_end:]
        if newline < 0:
            break
        cursor = line_end
    return None


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


def _median_ns(operation: Callable[[], object], rounds: int) -> int:
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
    """Run the selected benchmark matrix and emit one JSON report."""
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
    print(json.dumps(report, indent=2, sort_keys=True))  # noqa: T201


if __name__ == "__main__":
    main()
