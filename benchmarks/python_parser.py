"""Reproducible parsing benchmarks for the Python runtime."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from okf_parser.bundle import load_bundle
from okf_parser.discovery import discover_markdown
from okf_parser.exclusion import ExclusionRules
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


def _measure(
    size: int,
    body_paragraphs: int,
    rounds: int,
    read_concurrencies: list[int],
    rust_core: Path | None,
) -> dict[str, object]:
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
        paths = discover_markdown(root, ExclusionRules.read(root))
        discovery_ns = _median_ns(
            lambda: discover_markdown(root, ExclusionRules.read(root)),
            rounds,
        )
        sequential_read_ns = _median_ns(
            lambda: [path.read_bytes() for path in paths],
            rounds,
        )
        concurrent_read_ns: dict[str, int] = {}
        for concurrency in read_concurrencies:
            with ThreadPoolExecutor(max_workers=min(concurrency, len(paths))) as executor:
                elapsed = _median_ns(
                    lambda: list(executor.map(Path.read_bytes, paths)),
                    rounds,
                )
            concurrent_read_ns[str(concurrency)] = elapsed // len(paths)
        load_ns = _median_ns(lambda: load_bundle(root), rounds)
        if rust_core is not None:
            native_bundle = load_bundle(root)
            rust_bundle = load_bundle(root, rust_core=rust_core)
            for field in ("concepts", "reserved", "links"):
                native_rows = getattr(native_bundle, field).execute().to_dict(orient="records")
                rust_rows = getattr(rust_bundle, field).execute().to_dict(orient="records")
                if native_rows != rust_rows:
                    msg = f"native and Rust bundle {field} differ"
                    raise RuntimeError(msg)
            if native_bundle.diagnostics != rust_bundle.diagnostics:
                msg = "native and Rust bundle diagnostics differ"
                raise RuntimeError(msg)
        rust_load_ns = (
            _median_ns(lambda: load_bundle(root, rust_core=rust_core), rounds)
            if rust_core is not None
            else None
        )

    markdown_files = len(paths)
    result: dict[str, object] = {
        "documents": size,
        "markdown_files": markdown_files,
        "source_bytes": sum(len(source.encode()) for source in documents),
        "frontmatter_split_ns_per_document": split_ns // size,
        "document_parse_ns_per_document": parse_ns // size,
        "markdown_links_ns_per_document": links_ns // size,
        "markdown_headings_ns_per_document": headings_ns // size,
        "filesystem_discovery_ns_per_file": discovery_ns // markdown_files,
        "filesystem_sequential_read_ns_per_file": sequential_read_ns // markdown_files,
        "filesystem_read_ns_per_file_by_concurrency": concurrent_read_ns,
        "bundle_load_ns_per_document": load_ns // size,
        "bundle_load_ms": load_ns // 1_000_000,
    }
    if rust_load_ns is not None:
        result["rust_bundle_load_ns_per_document"] = rust_load_ns // size
        result["rust_bundle_load_ms"] = rust_load_ns // 1_000_000
        result["rust_bundle_speedup"] = load_ns / rust_load_ns
    return result


def main() -> None:
    """Run the selected benchmark matrix and emit one JSON report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="100,1000,5000")
    parser.add_argument("--body-paragraphs", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--read-concurrencies", default="32")
    parser.add_argument("--rust-core", type=Path)
    args = parser.parse_args()
    sizes = [int(value) for value in args.sizes.split(",") if value]
    read_concurrencies = [int(value) for value in args.read_concurrencies.split(",") if value]
    if not read_concurrencies or any(value < 1 for value in read_concurrencies):
        parser.error("--read-concurrencies must contain positive integers")

    report = {
        "runtime": "python",
        "runtime_version": platform.python_version(),
        "rounds": args.rounds,
        "body_paragraphs": args.body_paragraphs,
        "read_concurrencies": read_concurrencies,
        "results": [
            _measure(
                size,
                args.body_paragraphs,
                args.rounds,
                read_concurrencies,
                args.rust_core,
            )
            for size in sizes
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))  # noqa: T201


if __name__ == "__main__":
    main()
