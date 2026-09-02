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
from typing import TYPE_CHECKING, Any, Final

import networkx as nx

from okf_parser import load_bundle, validate_path

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _document(name: str, concept_type: str, targets: list[str]) -> str:
    """Render one concept carrying every field the ecosystem asks for.

    The tools disagree about where a link lives. okf-parser resolves Markdown
    links written in the body; okfquery reads a declared `links:` list from the
    frontmatter. Neither reading is wrong, so the fixture writes both and every
    tool sees the same six edges in whichever model it understands. Writing only
    one of them would measure which convention the fixture happened to pick.

    `description`, `generated` and `sources` are present because okfquery treats
    OKF v0.2 as requiring them. okf-parser preserves unknown frontmatter and
    requires only a non-empty `type`, so their presence changes nothing for it.
    """
    # An absent `links` key is not the same as an empty one: a tool that
    # validates its shape should not be handed `links: null`.
    declared = (
        "links:\n" + "".join(f"  - concepts/{target}.md\n" for target in targets) if targets else ""
    )
    body = "".join(f"- [{target}]({target}.md)\n" for target in targets)
    return (
        "---\n"
        f"type: {concept_type}\n"
        f"title: {name}\n"
        f"description: Fixture concept {name}\n"
        "generated:\n"
        "  by: capability-matrix\n"
        "  at: 2026-09-02T00:00:00Z\n"
        "sources:\n"
        "  - resource: capability-matrix fixture\n"
        f"{declared}"
        "---\n"
        f"\n# {name}\n\n{body}"
    )


def build_fixture(root: Path) -> None:
    """Write the deterministic bundle that the expected answers describe."""
    concepts = root / "concepts"
    concepts.mkdir(parents=True)
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    for name, (concept_type, targets) in CONCEPTS.items():
        (concepts / f"{name}.md").write_text(
            _document(name, concept_type, targets), encoding="utf-8"
        )
    types = root / "docs" / "types"
    types.mkdir(parents=True)
    for concept_type in SPEC_TYPES:
        (types / f"{concept_type.lower()}.md").write_text(
            _document(concept_type.lower(), "Spec", []), encoding="utf-8"
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
class Attempt:
    """One rival invocation and how to read the answer out of its output."""

    argv: tuple[str, ...]
    extract: Callable[[int, str], Any] | None = None


@dataclass(frozen=True)
class Rival:
    """An ecosystem tool, how to run it, and which questions it can reach."""

    name: str
    executable: str
    surface: tuple[str, ...]
    answers: dict[str, Attempt] = field(default_factory=dict)


def _rows(output: str) -> list[dict[str, Any]]:
    """Read the JSON row list an okfquery `--format json` run prints."""
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _column(output: str, name: str) -> list[str]:
    """Project one column, reduced to the fixture's short names."""
    return sorted(_stem(row[name]) for row in _rows(output) if row.get(name) is not None)


# okfquery's interface *is* SQL, so answering it in SQL is answering it on its
# own terms. Post-processing another tool's output inside this harness would be
# implementing the feature on its behalf, which is not the same thing and is not
# done here: a tool that only prints a graph is recorded as not answering.
_CYCLE_SQL: Final = (
    "with recursive walk(start, cur, depth) as ("
    "select path, target, 1 from links "
    "union all "
    "select w.start, l.target, w.depth + 1 from walk w join links l on l.path = w.cur "
    "where w.depth < 10) "
    "select distinct start from walk where cur = start order by 1"
)
_IMPACT_SQL: Final = (
    "with recursive anc(p) as ("
    "select path from links where target = 'concepts/a.md' "
    "union "
    "select l.path from links l join anc on l.target = anc.p) "
    "select p from anc where p <> 'concepts/a.md' order by 1"
)


def _okfquery(sql: str, extract: Callable[[int, str], Any]) -> Attempt:
    """Build an okfquery attempt that runs one SQL statement."""
    return Attempt(("query", sql, "--bundle", "{root}", "--format", "json"), extract)


RIVALS: Final = (
    Rival(
        "kbforge-okfquery",
        "okfquery",
        ("query", "shell", "schema", "check"),
        {
            "conformant": Attempt(("check", "--bundle", "{root}"), lambda code, _: code == 0),
            "concept_count": _okfquery(
                "select count(*) as n from concepts",
                lambda _, out: _rows(out)[0]["n"] if _rows(out) else None,
            ),
            "type_counts": _okfquery(
                "select type, count(*) as n from concepts group by 1 order by 1",
                lambda _, out: {row["type"]: row["n"] for row in _rows(out)},
            ),
            "no_inbound": _okfquery(
                "select path from concepts where path not in (select target from links) order by 1",
                lambda _, out: _column(out, "path"),
            ),
            "cycles": _okfquery(
                _CYCLE_SQL,
                lambda _, out: [_column(out, "start")] if _rows(out) else [],
            ),
            "unresolved_links": _okfquery(
                "select count(*) as n from links where target not in (select path from concepts)",
                lambda _, out: _rows(out)[0]["n"] if _rows(out) else None,
            ),
            "types_without_spec": _okfquery(
                "select distinct type from concepts c where not exists ("
                "select 1 from concepts s where s.path = 'docs/types/' || lower(c.type) || '.md') "
                "order by 1",
                lambda _, out: sorted(row["type"] for row in _rows(out)),
            ),
            "impact_of_deleting_a": _okfquery(_IMPACT_SQL, lambda _, out: _column(out, "p")),
        },
    ),
    Rival(
        "okflint",
        "okflint",
        ("audit", "validate", "validate-manifest", "index"),
        {
            "conformant": Attempt(
                ("validate", "--manifest", "{root}/okf-base.yaml", "{root}"),
                lambda code, _: code == 0,
            ),
            "concept_count": Attempt(("audit", "--manifest", "{root}/okf-base.yaml")),
        },
    ),
    Rival(
        "okf-retrieve",
        "okf",
        ("validate", "search", "graph", "serve-mcp"),
        {"conformant": Attempt(("validate", "{root}"), lambda code, _: code == 0)},
    ),
    Rival(
        "okf-schema",
        "okf-schema",
        ("backlinks", "index", "init", "kb"),
        {},
    ),
    Rival(
        "okf-nav",
        "okf-nav",
        ("search", "show", "status", "topics", "health", "audit", "export", "index"),
        {"concept_count": Attempt(("status",))},
    ),
    Rival(
        "google-okf",
        "google-okf",
        ("init", "lint", "produce"),
        {"conformant": Attempt(("lint", "{root}"), lambda code, _: code == 0)},
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


def _run(argv: list[str]) -> tuple[int, str]:
    """Run a rival command, returning its exit status and combined output."""
    try:
        completed = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, check=False, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def probe_rival(rival: Rival, root: Path, binaries: Path | None) -> dict[str, Any]:
    """Record, per question, whether this rival exposes any way to answer it."""
    executable = _resolve(rival.executable, binaries)
    if executable is None:
        return {"installed": False, "surface": list(rival.surface), "results": {}}

    results: dict[str, Any] = {}
    for question in QUESTIONS:
        attempt = rival.answers.get(question.key)
        if attempt is None:
            results[question.key] = {
                "verdict": "unsupported",
                "reason": f"no subcommand in {list(rival.surface)} exposes this",
            }
            continue
        rendered = [part.replace("{root}", str(root)) for part in attempt.argv]
        status, output = _run([executable, *rendered])
        entry: dict[str, Any] = {"exit_status": status, "output_head": output.splitlines()[:3]}
        if attempt.extract is None:
            # The tool ran and produced something a person could read, but not a
            # value this harness can compare. Graded no further than that.
            entry["verdict"] = "unscored"
        else:
            try:
                produced = attempt.extract(status, output)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                entry["verdict"] = "error"
                entry["reason"] = f"{type(exc).__name__}: {exc}"
            else:
                expected = EXPECTED[question.key]
                entry["verdict"] = "correct" if produced == expected else "disagrees"
                entry["produced"] = produced
                if produced != expected:
                    entry["expected"] = expected
        results[question.key] = entry
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
