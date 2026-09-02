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

The axis that separates these tools is capability. This benchmark asks the same
questions of every tool and records, per question, whether it answered
correctly, produced a different answer, or exposes no surface that could answer
at all. Each rival's advertised command surface is recorded next to its
verdicts, so `unsupported` is auditable rather than asserted.

Three properties keep the comparison honest, and the first two exist because the
first published run failed them.

**Every rival gets its own environment.** Three of these tools -- `okf-cli`,
`okf-retrieve` and `okf-generator` -- install an executable named `okf`.
Installing them together leaves whichever landed last, so the harness provisions
one virtual environment per rival and never shares a `PATH`.

**Every rival gets the configuration it asks for.** `okflint` needs an
`okf-base.yaml` manifest; `okf-nav` reads bundles from an `OKF_BUNDLES_DIR`
rather than from an argument; `okf-generator` compares two bundles, so it is
handed a second one with a concept removed. A tool that was not shown the
fixture the way it expects has not been measured, and recording that as an
incapability is a defect in the harness rather than a finding about the tool.

**The fixture can fail okf-parser.** Every expected answer is a property of the
documents the fixture writes, and the script exits non-zero when okf-parser
disagrees with them.

This measures the *published* okf-parser: the PEP 723 header above resolves the
dependency from an index, so the working tree is not what runs unless it has
been installed into the environment. The report records the version and import
location actually measured, and reading this as a check on the current checkout
would be wrong.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import networkx as nx

import okf_parser
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
    tool sees the same edges in whichever model it understands. Writing only one
    of them would measure which convention the fixture happened to pick.

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


@dataclass(frozen=True)
class Layout:
    """Where the fixture and its variants live for one run."""

    root: Path
    bundles_dir: Path
    minus_a: Path

    def placeholders(self) -> dict[str, str]:
        """Substitutions available to a rival's argument vector and environment."""
        return {
            "{root}": str(self.root),
            "{bundles_dir}": str(self.bundles_dir),
            "{root_minus_a}": str(self.minus_a),
        }


def build_layout(work: Path) -> Layout:
    """Lay the fixture out so every rival can be shown it the way it expects."""
    bundles = work / "bundles"
    root = bundles / "fixture"
    build_fixture(root)
    minus_a = work / "variants" / "fixture-minus-a"
    minus_a.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root, minus_a)
    (minus_a / "concepts" / "a.md").unlink()
    return Layout(root=root, bundles_dir=bundles, minus_a=minus_a)


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
# Rivals. Surfaces are transcribed from each tool's own --help output, and are
# kept in step with benchmarks/rivals/ by tests/test_rivals_registry.py.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attempt:
    """One rival invocation and how to read the answer out of its output."""

    argv: tuple[str, ...]
    extract: Callable[[int, str], Any] | None = None


@dataclass(frozen=True)
class Rival:
    """An ecosystem tool, how to install and run it, and what it can reach."""

    name: str
    distribution: str
    executable: str
    surface: tuple[str, ...]
    answers: dict[str, Attempt] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)


_ANSI: Final = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    """Strip the terminal colouring some rivals emit even when piped."""
    return _ANSI.sub("", output)


def _payload(output: str, opener: str) -> object:
    """Read the JSON document a rival prints after any banner it insists on."""
    text = _plain(output)
    start = text.find(opener)
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


def _rows(output: str) -> list[dict[str, Any]]:
    """Read the JSON row list an okfquery `--format json` run prints."""
    parsed = _payload(output, "[")
    return parsed if isinstance(parsed, list) else []


def _column(output: str, name: str) -> list[str]:
    """Project one column, reduced to the fixture's short names."""
    return sorted(_stem(row[name]) for row in _rows(output) if row.get(name) is not None)


def _first(output: str, name: str) -> object:
    """Read one scalar out of a single-row result."""
    rows = _rows(output)
    return rows[0][name] if rows else None


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


def _nav_total(_: int, output: str) -> int | None:
    """Read the concept total out of an okf-nav status table."""
    match = re.search(r"^TOTAL\s+(\d+)", _plain(output), re.MULTILINE)
    return int(match.group(1)) if match else None


def _nav_types(_: int, output: str) -> dict[str, int]:
    """Read the type distribution out of an okf-nav status table."""
    _, marker, tail = _plain(output).partition("Type distribution:")
    if not marker:
        return {}
    return {
        name: int(count) for name, count in re.findall(r"^\s+(\w+):\s+(\d+)$", tail, re.MULTILINE)
    }


def _generator_concepts(output: str) -> list[dict[str, Any]]:
    """Read the concept list an okf-generator lookup prints as JSON."""
    parsed = _payload(output, "[")
    return parsed if isinstance(parsed, list) else []


def _generator_count(_: int, output: str) -> int | None:
    """Count the concepts an okf-generator lookup returned."""
    return len(_generator_concepts(output)) or None


def _generator_types(_: int, output: str) -> dict[str, int]:
    """Tally the concept types an okf-generator lookup returned."""
    concepts = _generator_concepts(output)
    kinds = sorted({str(concept.get("type")) for concept in concepts})
    return {kind: sum(1 for c in concepts if c.get("type") == kind) for kind in kinds}


def _generator_impact(_: int, output: str) -> list[str]:
    """Read the concepts an okf-generator impact diff reports as affected."""
    parsed = _payload(output, "{")
    if not isinstance(parsed, dict):
        return []
    impact = parsed.get("impact", {})
    affected = list(impact.get("changed_deps", [])) + list(impact.get("removed_deps", []))
    return sorted(_stem(entry.get("concept_id", entry)) for entry in affected)


_LOOKUP: Final = ("lookup", "--bundle", "{root}", "--json", "--limit", "100")

RIVALS: Final = (
    Rival(
        "kbforge-okfquery",
        "kbforge-okfquery",
        "okfquery",
        ("query", "shell", "schema", "check"),
        {
            "conformant": Attempt(("check", "--bundle", "{root}"), lambda code, _: code == 0),
            "concept_count": _okfquery(
                "select count(*) as n from concepts", lambda _, out: _first(out, "n")
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
                _CYCLE_SQL, lambda _, out: [_column(out, "start")] if _rows(out) else []
            ),
            "unresolved_links": _okfquery(
                "select count(*) as n from links where target not in (select path from concepts)",
                lambda _, out: _first(out, "n"),
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
        "okf-generator",
        "okf-generator",
        "okf",
        (
            "generate",
            "update",
            "domains",
            "enrich",
            "lsp",
            "lookup",
            "ask",
            "diff",
            "pairs",
            "summarize",
            "install",
            "init",
            "visualize",
            "serve",
            "dashboard",
            "mcp",
            "plugin",
        ),
        {
            "concept_count": Attempt(_LOOKUP, _generator_count),
            "type_counts": Attempt(_LOOKUP, _generator_types),
            "impact_of_deleting_a": Attempt(
                ("diff", "{root}", "{root_minus_a}", "--impact", "--json"), _generator_impact
            ),
        },
    ),
    Rival(
        "okflint",
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
        "okf-nav",
        "okf-nav",
        "okf-nav",
        (
            "search",
            "show",
            "status",
            "topics",
            "health",
            "audit",
            "export",
            "index",
            "update",
            "stale",
            "context",
        ),
        {
            "concept_count": Attempt(("status",), _nav_total),
            "type_counts": Attempt(("status",), _nav_types),
        },
        # okf-nav discovers bundles through the environment rather than through
        # an argument. Passing the bundle positionally, as the first published
        # run did, makes it report that it found none, which says nothing about
        # what it can do.
        env={"OKF_BUNDLES_DIR": "{bundles_dir}"},
    ),
    Rival(
        "okf-cli",
        "okf-cli",
        "okf",
        ("bundle", "list", "read", "validate"),
        {
            "conformant": Attempt(("validate", "{root}"), lambda code, _: code == 0),
            "concept_count": Attempt(("list", "{root}")),
        },
    ),
    Rival(
        "okf-retrieve",
        "okf-retrieve",
        "okf",
        ("validate", "search", "graph", "serve-mcp"),
        {"conformant": Attempt(("validate", "{root}"), lambda code, _: code == 0)},
    ),
    Rival(
        "okf-schema",
        "okf-schema",
        "okf-schema",
        ("backlinks", "index", "init", "install-skills", "kb"),
        {},
    ),
    Rival(
        "google-okf",
        "google-okf",
        "google-okf",
        ("init", "lint", "produce"),
        {"conformant": Attempt(("lint", "{root}"), lambda code, _: code == 0)},
    ),
)


# ---------------------------------------------------------------------------
# Running.
# ---------------------------------------------------------------------------


def _run(argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run a command, returning its exit status and combined output."""
    merged = {**os.environ, **(env or {})}
    try:
        completed = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, check=False, timeout=300, env=merged
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def provision(rival: Rival, environments: Path) -> str | None:
    """Install one rival into a virtual environment of its own.

    Three of these tools install an executable named `okf`. Sharing one
    environment would silently measure whichever was installed last, so each
    gets its own and none is ever resolved through a shared PATH.
    """
    venv = environments / rival.name
    if _run(["uv", "venv", "--python", "3.12", str(venv)])[0] != 0:
        return None
    python = next(
        (p for p in (venv / "Scripts" / "python.exe", venv / "bin" / "python") if p.exists()),
        None,
    )
    if python is None:
        return None
    if _run(["uv", "pip", "install", "--python", str(python), rival.distribution])[0] != 0:
        return None
    for candidate in (
        python.parent / f"{rival.executable}.exe",
        python.parent / rival.executable,
    ):
        if candidate.exists():
            return str(candidate)
    return None


def probe_rival(rival: Rival, layout: Layout, executable: str | None) -> dict[str, Any]:
    """Record, per question, whether this rival exposes any way to answer it."""
    if executable is None:
        return {"installed": False, "surface": list(rival.surface), "results": {}}

    substitutions = layout.placeholders()

    def render(value: str) -> str:
        for token, replacement in substitutions.items():
            value = value.replace(token, replacement)
        return value

    env = {key: render(value) for key, value in rival.env.items()}
    results: dict[str, Any] = {}
    for question in QUESTIONS:
        attempt = rival.answers.get(question.key)
        if attempt is None:
            results[question.key] = {
                "verdict": "unsupported",
                "reason": f"no subcommand in {list(rival.surface)} exposes this",
            }
            continue
        status, output = _run([executable, *(render(part) for part in attempt.argv)], env)
        entry: dict[str, Any] = {
            "exit_status": status,
            "output_head": _plain(output).splitlines()[:3],
        }
        if attempt.extract is None:
            # The tool ran and produced something a person could read, but not a
            # value this harness can compare. Graded no further than that.
            entry["verdict"] = "unscored"
        else:
            try:
                produced = attempt.extract(status, output)
            except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
                entry["verdict"] = "error"
                entry["reason"] = f"{type(exc).__name__}: {exc}"
            else:
                expected = EXPECTED[question.key]
                entry["verdict"] = "correct" if produced == expected else "disagrees"
                entry["produced"] = produced
                if produced != expected:
                    entry["expected"] = expected
        results[question.key] = entry
    return {
        "installed": True,
        "surface": list(rival.surface),
        "environment": rival.env or None,
        "results": results,
    }


def main() -> int:
    """Run the matrix, print it as JSON, and fail when okf-parser is wrong."""
    parser = argparse.ArgumentParser(description="OKF capability matrix")
    parser.add_argument(
        "--environments",
        type=Path,
        default=None,
        help="directory to provision one virtual environment per rival into "
        "(default: a temporary directory discarded afterwards)",
    )
    parser.add_argument(
        "--skip-rivals",
        action="store_true",
        help="answer with okf-parser only, installing nothing",
    )
    arguments = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="okf-capability-") as directory:
        work = Path(directory)
        layout = build_layout(work)
        produced = answer_with_okf_parser(layout.root)
        rivals: dict[str, Any] = {}
        if not arguments.skip_rivals:
            environments = arguments.environments or (work / "environments")
            environments.mkdir(parents=True, exist_ok=True)
            for rival in RIVALS:
                rivals[rival.name] = probe_rival(rival, layout, provision(rival, environments))

    okf_results = {
        question.key: {
            "verdict": "correct" if produced[question.key] == EXPECTED[question.key] else "wrong",
            "produced": produced[question.key],
            "expected": EXPECTED[question.key],
        }
        for question in QUESTIONS
    }
    report = {
        "schema_version": 2,
        # Which okf-parser this run measured. The PEP 723 header resolves the
        # dependency from an index, so this is normally the published package
        # rather than the working tree.
        "measured": {
            "okf_parser_version": distribution_version("okf-parser"),
            "okf_parser_location": str(Path(okf_parser.__file__).resolve().parent),
        },
        "questions": [{"key": q.key, "prompt": q.prompt} for q in QUESTIONS],
        "tools": {"okf-parser": {"installed": True, "results": okf_results}, **rivals},
    }
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if all(r["verdict"] == "correct" for r in okf_results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
