#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "okf-parser",
#     "networkx>=3.4,<4",
# ]
# ///
"""Measure which questions each OKF tool can answer, not how fast it answers.

The other benchmarks in this directory compare okf-parser's own Python,
TypeScript and Rust implementations. They say nothing about the rest of the
ecosystem, and a latency comparison against it would measure the wrong thing: a
linter reads a document and checks a rule, while okf-parser compiles a bundle
into relational tables and a link graph. Being slower than a linter is a
consequence of doing more, not a defect.

The axis that actually separates these tools is capability. This benchmark asks
the same questions of every installed tool and records, per question, whether
the tool answered correctly, answered wrongly, or exposes no surface that could
answer at all. Each rival's advertised command surface is recorded next to its
verdicts, so `unsupported` is auditable rather than asserted.

The fixture is built so that every expected answer is a property of the
documents it writes, which means okf-parser can fail this benchmark too. It
exits non-zero when it does.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import networkx as nx

from okf_parser import load_bundle, validate_path

# ---------------------------------------------------------------------------
# Fixture. Every expected answer below is a property of these six documents.
# ---------------------------------------------------------------------------

CONCEPTS: Final = {
    "a": ("Service", ["b"]),
    "b": ("Service", ["c"]),
    "c": ("Service", ["a"]),
    "d": ("Record", ["a"]),
    "e": ("Record", []),
    "f": ("Ledger", ["missing"]),
}
# A specification document is itself a concept of type `Spec`, so it is part of
# the bundle it describes and shows up in every count below. `Spec` therefore
# needs its own specification, and `Ledger` is left without one deliberately so
# that the coverage question has something to find.
SPEC_TYPES: Final = ("Service", "Record", "Spec")

EXPECTED: Final = {
    "conformant": True,
    "concept_count": 9,
    "type_counts": {"Ledger": 1, "Record": 2, "Service": 3, "Spec": 3},
    "no_inbound": ["d", "e", "f", "record", "service", "spec"],
    "cycles": [["a", "b", "c"]],
    "unresolved_links": 1,
    "types_without_spec": ["Ledger"],
    "impact_of_deleting_a": ["b", "c", "d"],
}


def build_fixture(root: Path) -> None:
    """Write the deterministic bundle that the expected answers describe."""
    concepts = root / "concepts"
    concepts.mkdir(parents=True)
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    for name, (concept_type, targets) in CONCEPTS.items():
        links = "".join(f"- [{target}]({target}.md)\n" for target in targets)
        (concepts / f"{name}.md").write_text(
            f"---\ntype: {concept_type}\ntitle: {name}\n---\n\n# {name}\n\n{links}",
            encoding="utf-8",
        )
    types = root / "docs" / "types"
    types.mkdir(parents=True)
    for concept_type in SPEC_TYPES:
        (types / f"{concept_type.lower()}.md").write_text(
            f"---\ntype: Spec\ntitle: {concept_type}\n---\n\n# {concept_type}\n",
            encoding="utf-8",
        )
    # okflint operates on manifest-declared bases rather than on a bare
    # directory, so the fixture ships the manifest it asks for. A rival that
    # cannot even start is not evidence about capability.
    (root / "okf-base.yaml").write_text(
        'okf_version: "0.2"\n'
        "base:\n"
        "  name: capability-matrix\n"
        "  roots:\n"
        "    - path: .\n"
        "  reserved_files:\n"
        "    index: index.md\n"
        "    log: log.md\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Questions.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    """One question put to every tool."""

    key: str
    prompt: str


QUESTIONS: Final = (
    Question("conformant", "Is the bundle conformant?"),
    Question("concept_count", "How many concepts does it contain?"),
    Question("type_counts", "How many concepts of each type?"),
    Question("no_inbound", "Which concepts have no inbound link?"),
    Question("cycles", "Are there cycles in the link graph?"),
    Question("unresolved_links", "How many links do not resolve?"),
    Question("types_without_spec", "Which types in use have no specification?"),
    Question("impact_of_deleting_a", "What breaks if concept 'a' is deleted?"),
)


def _stem(identifier: object) -> str:
    """Reduce a concept identifier to the short name the fixture uses."""
    return Path(str(identifier)).stem


def answer_with_okf_parser(root: Path) -> dict[str, Any]:
    """Answer every question through the public okf-parser API."""
    report = validate_path(root, require_spec="docs/types/{slug}.md")
    bundle = load_bundle(root)
    concepts = bundle.concepts.execute()
    links = bundle.links.execute()
    graph = bundle.to_networkx()

    types = sorted({str(value) for value in concepts["concept_type"]})
    specified = {concept_type.lower() for concept_type in SPEC_TYPES}
    names = {_stem(value) for value in concepts["concept_id"]}

    resolved = links[links["target_id"].notna()]
    targets = {_stem(value) for value in resolved["target_id"]}

    edges = ((_stem(u), _stem(v)) for u, v in graph.edges())
    simple = nx.DiGraph((u, v) for u, v in edges if u in names and v in names)
    simple.add_nodes_from(names)

    return {
        "conformant": bool(report.is_conformant),
        "concept_count": int(report.concept_count),
        "type_counts": {
            concept_type: int((concepts["concept_type"] == concept_type).sum())
            for concept_type in types
        },
        "no_inbound": sorted(names - targets),
        "cycles": sorted(sorted(cycle) for cycle in nx.simple_cycles(simple)),
        "unresolved_links": int(len(links) - len(resolved)),
        "types_without_spec": sorted(t for t in types if t.lower() not in specified),
        "impact_of_deleting_a": sorted(nx.ancestors(simple, "a")),
    }


# ---------------------------------------------------------------------------
# Rivals. Surfaces are transcribed from each tool's own --help output.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rival:
    """An ecosystem tool, how to run it, and which questions it can reach."""

    name: str
    executable: str
    surface: tuple[str, ...]
    answers: dict[str, tuple[str, ...]] = field(default_factory=dict)


RIVALS: Final = (
    Rival(
        "okflint",
        "okflint",
        ("audit", "validate", "validate-manifest", "index"),
        {
            "conformant": ("validate", "--manifest", "{root}/okf-base.yaml", "{root}"),
            "concept_count": ("audit", "--manifest", "{root}/okf-base.yaml"),
        },
    ),
    Rival(
        "okf-cli",
        "okf",
        ("bundle", "list", "read", "validate"),
        {"conformant": ("validate", "{root}"), "concept_count": ("list", "{root}")},
    ),
    Rival(
        "google-okf",
        "google-okf",
        ("init", "lint", "produce"),
        {"conformant": ("lint", "{root}")},
    ),
)


def _resolve(executable: str, binaries: Path | None) -> str | None:
    """Return a runnable path for a rival, or None when it is not installed."""
    if binaries is not None:
        for candidate in (binaries / f"{executable}.exe", binaries / executable):
            if candidate.exists():
                return str(candidate)
        return None
    return shutil.which(executable)


def _run(argv: list[str]) -> tuple[int, list[str]]:
    """Run a rival command, returning its status and the head of its output."""
    try:
        completed = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, check=False, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, [str(exc)]
    combined = (completed.stdout + completed.stderr).strip()
    return completed.returncode, combined.splitlines()[:3]


def probe_rival(rival: Rival, root: Path, binaries: Path | None) -> dict[str, Any]:
    """Record, per question, whether this rival exposes any way to answer it."""
    executable = _resolve(rival.executable, binaries)
    if executable is None:
        return {"installed": False, "surface": list(rival.surface), "results": {}}

    results: dict[str, Any] = {}
    for question in QUESTIONS:
        argv = rival.answers.get(question.key)
        if argv is None:
            results[question.key] = {
                "verdict": "unsupported",
                "reason": f"no subcommand in {list(rival.surface)} exposes this",
            }
            continue
        rendered = [part.replace("{root}", str(root)) for part in argv]
        status, head = _run([executable, *rendered])
        results[question.key] = {
            "verdict": "attempted",
            "exit_status": status,
            "output_head": head,
        }
    return {"installed": True, "surface": list(rival.surface), "results": results}


def main() -> int:
    """Run the matrix, print it as JSON, and fail when okf-parser is wrong."""
    parser = argparse.ArgumentParser(description="OKF capability matrix")
    parser.add_argument(
        "--rival-bin",
        type=Path,
        default=None,
        help="directory holding the rival executables when they are not on PATH",
    )
    arguments = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="okf-capability-") as directory:
        root = Path(directory) / "bundle"
        build_fixture(root)
        produced = answer_with_okf_parser(root)
        rivals = {rival.name: probe_rival(rival, root, arguments.rival_bin) for rival in RIVALS}

    okf_results = {
        question.key: {
            "verdict": "correct" if produced[question.key] == EXPECTED[question.key] else "wrong",
            "produced": produced[question.key],
            "expected": EXPECTED[question.key],
        }
        for question in QUESTIONS
    }
    report = {
        "schema_version": 1,
        "questions": [{"key": q.key, "prompt": q.prompt} for q in QUESTIONS],
        "tools": {"okf-parser": {"installed": True, "results": okf_results}, **rivals},
    }
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if all(r["verdict"] == "correct" for r in okf_results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
