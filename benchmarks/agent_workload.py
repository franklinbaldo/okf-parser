# /// script
# requires-python = ">=3.12"
# ///
"""Benchmark realistic agent tasks across OKF tools and a Bash/Python baseline."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_PROVENANCE_ENV = {
    "okfcli": "OKFCLI_PROVENANCE",
    "skosovsky-okf": "SKOSOVSKY_OKF_PROVENANCE",
    "okf-generator": "OKF_GENERATOR_PROVENANCE",
}
_QUERY_TIMEOUT_SECONDS = 180
_TYPES = tuple(f"Type{index}" for index in range(8))


@dataclass(frozen=True, slots=True)
class Task:
    """One user/agent goal rather than one implementation primitive."""

    name: str
    description: str
    mutable: bool = False


@dataclass(frozen=True, slots=True)
class Adapter:
    """One tool surface participating in the workload benchmark."""

    name: str
    command: tuple[str, ...]
    version_args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Invocation:
    """A sequence of subprocesses that completes one task."""

    commands: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class Sample:
    """One timed task execution."""

    elapsed_ns: int
    stdout_bytes: int
    stderr_bytes: int
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Immutable corpus and timing parameters for one benchmark run."""

    documents: int
    body_bytes: int
    rounds: int
    warmups: int
    target_index: int


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Derived paths and values shared by adapter command builders."""

    root: Path
    documents: int
    target_id: str
    target_title: str
    target_type: str
    target_path: Path


_TASKS = (
    Task("validate", "Validate a complete authored OKF bundle."),
    Task("inventory", "Count/list concepts by producer-defined type."),
    Task("lookup", "Retrieve one known concept with enough context to act."),
    Task("filter_type", "Find concepts of one requested type."),
    Task("backlinks", "Find concepts that link to one requested concept."),
    Task("graph", "Get a bundle-level relation picture."),
    Task("edit_validate", "Change one title and validate the resulting bundle.", mutable=True),
)


def _command_from_env(variable: str, default: str) -> tuple[str, ...]:
    """Resolve a configured executable command without invoking a shell."""
    parts = shlex.split(os.environ.get(variable, default))
    if not parts:
        return ()
    executable = shutil.which(parts[0])
    return (executable, *parts[1:]) if executable is not None else tuple(parts)


def _adapters() -> tuple[Adapter, ...]:
    root = Path(__file__).resolve().parent
    bash = shutil.which("bash") or "bash"
    return (
        Adapter("bash-python", (bash, str(root / "generic_agent_baseline.sh")), ()),
        Adapter("okf-parser", _command_from_env("OKF_PARSER_CMD", "okf-parser"), ("--version",)),
        Adapter("okfcli", _command_from_env("OKFCLI_CMD", "okfcli-bench"), ("version",)),
        Adapter(
            "skosovsky-okf",
            _command_from_env("SKOSOVSKY_OKF_CMD", "skosovsky-okf-bench"),
            ("version",),
        ),
        Adapter(
            "okf-generator",
            _command_from_env("OKF_GENERATOR_CMD", "okf-generator-bench"),
            ("--version",),
        ),
    )


def _concept_id(index: int) -> str:
    return f"group-{index % 16:02d}/concept-{index:08d}"


def _title(index: int) -> str:
    return f"Benchmark Concept {index:08d}"


def _context(root: Path, config: RunConfig) -> TaskContext:
    target_id = _concept_id(config.target_index)
    return TaskContext(
        root=root,
        documents=config.documents,
        target_id=target_id,
        target_title=_title(config.target_index),
        target_type=_TYPES[config.target_index % len(_TYPES)],
        target_path=root / f"{target_id}.md",
    )


def _write_bundle(root: Path, documents: int, body_bytes: int) -> None:
    """Generate a deterministic common-subset OKF bundle for every adapter."""
    filler = "x" * max(0, body_bytes - 180)
    for index in range(documents):
        concept_id = _concept_id(index)
        path = root / f"{concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        next_id = _concept_id((index + 1) % documents)
        second_id = _concept_id((index + 17) % documents)
        next_rel = os.path.relpath(root / f"{next_id}.md", path.parent).replace(os.sep, "/")
        second_rel = os.path.relpath(root / f"{second_id}.md", path.parent).replace(os.sep, "/")
        text = (
            "---\n"
            f"type: {_TYPES[index % len(_TYPES)]}\n"
            f"title: {_title(index)}\n"
            f"description: Deterministic benchmark concept {index}.\n"
            "---\n\n"
            f"# {_title(index)}\n\n"
            f"Uses [next]({next_rel}) and [secondary]({second_rel}).\n\n"
            f"{filler}\n"
        )
        path.write_text(text, encoding="utf-8")


def _available(adapter: Adapter) -> bool:
    if not adapter.command:
        return False
    return Path(adapter.command[0]).is_file() or shutil.which(adapter.command[0]) is not None


def _reported_version(adapter: Adapter) -> str:
    if adapter.name == "bash-python":
        return f"bash + Python {platform.python_version()} stdlib"
    if not _available(adapter):
        return "unavailable"
    completed = subprocess.run(  # noqa: S603 -- benchmark commands are controlled adapters.
        [*adapter.command, *adapter.version_args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    raw = (completed.stdout or completed.stderr).strip()
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("version"):
            return str(payload["version"])
    lines = raw.splitlines()
    return lines[0] if lines else f"exit-{completed.returncode}"


def _provenance(adapter: Adapter) -> str:
    """Return install provenance independently from executable version output."""
    variable = _PROVENANCE_ENV.get(adapter.name)
    return os.environ.get(variable, "") if variable is not None else ""


def _bash_invocation(base: tuple[str, ...], task: Task, ctx: TaskContext) -> Invocation | None:
    operation = {
        "validate": "validate",
        "inventory": "inventory",
        "lookup": "show",
        "filter_type": "type",
        "backlinks": "backlinks",
        "graph": "graph",
    }.get(task.name)
    if task.name == "edit_validate":
        return Invocation(
            (
                (*base, "edit-title", str(ctx.root), f"{ctx.target_id}=Updated Benchmark Title"),
                (*base, "validate", str(ctx.root)),
            )
        )
    if operation is None:
        return None
    value = {
        "lookup": ctx.target_id,
        "backlinks": ctx.target_id,
        "filter_type": ctx.target_type,
    }.get(task.name, "")
    return Invocation(((*base, operation, str(ctx.root), value),))


def _parser_invocation(base: tuple[str, ...], task: Task, ctx: TaskContext) -> Invocation | None:
    commands = {
        "validate": (*base, "check", str(ctx.root)),
        "inventory": (*base, "inventory", str(ctx.root)),
        "graph": (*base, "graph", str(ctx.root)),
    }
    command = commands.get(task.name)
    if command is not None:
        return Invocation((command,))
    if task.name != "edit_validate":
        return None
    return Invocation(
        (
            (
                *base,
                "apply",
                str(ctx.root),
                "--type",
                ctx.target_type,
                "--field",
                "title",
                "--from",
                ctx.target_title,
                "--to",
                "Updated Benchmark Title",
                "--write",
            ),
            (*base, "check", str(ctx.root)),
        )
    )


def _okfcli_invocation(base: tuple[str, ...], task: Task, ctx: TaskContext) -> Invocation | None:
    commands = {
        "validate": (*base, "validate", str(ctx.root)),
        "inventory": (*base, "list", str(ctx.root)),
        "lookup": (*base, "show", str(ctx.root), ctx.target_id),
        "filter_type": (*base, "search", str(ctx.root), "--type", ctx.target_type),
        "backlinks": (*base, "backlinks", str(ctx.root), ctx.target_id),
        "graph": (*base, "graph", str(ctx.root)),
    }
    command = commands.get(task.name)
    return Invocation((command,)) if command is not None else None


def _skosovsky_invocation(base: tuple[str, ...], task: Task, ctx: TaskContext) -> Invocation | None:
    commands = {
        "validate": (*base, "validate", "-path", str(ctx.root)),
        "inventory": (*base, "info", str(ctx.root)),
        "lookup": (*base, "parse", str(ctx.target_path)),
        "graph": (*base, "graph", str(ctx.root), "-format", "json-ld"),
    }
    command = commands.get(task.name)
    return Invocation((command,)) if command is not None else None


def _generator_invocation(base: tuple[str, ...], task: Task, ctx: TaskContext) -> Invocation | None:
    if task.name == "lookup":
        command = (
            *base,
            "lookup",
            "--bundle",
            str(ctx.root),
            "--json",
            "--exact",
            ctx.target_title,
        )
        return Invocation((command,))
    if task.name != "filter_type":
        return None
    command = (
        *base,
        "lookup",
        "--bundle",
        str(ctx.root),
        "--json",
        "--type",
        ctx.target_type,
        "--limit",
        str(ctx.documents),
    )
    return Invocation((command,))


_BUILDERS = {
    "bash-python": _bash_invocation,
    "okf-parser": _parser_invocation,
    "okfcli": _okfcli_invocation,
    "skosovsky-okf": _skosovsky_invocation,
    "okf-generator": _generator_invocation,
}


def _invocation(adapter: Adapter, task: Task, ctx: TaskContext) -> Invocation | None:
    builder = _BUILDERS[adapter.name]
    return builder(adapter.command, task, ctx)


def _run(invocation: Invocation) -> Sample:
    started = time.perf_counter_ns()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    returncode = 0
    for command in invocation.commands:
        completed = subprocess.run(  # noqa: S603 -- benchmark commands are controlled adapters.
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=_QUERY_TIMEOUT_SECONDS,
        )
        stdout_parts.append(completed.stdout)
        stderr_parts.append(completed.stderr)
        returncode = completed.returncode
        if returncode:
            break
    elapsed = time.perf_counter_ns() - started
    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    return Sample(elapsed, len(stdout.encode()), len(stderr.encode()), returncode, stdout, stderr)


def _percentile(values: Sequence[int], percentile: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


def _verify(task: Task, adapter: Adapter, sample: Sample, ctx: TaskContext) -> bool:
    if sample.returncode != 0:
        return False
    output = sample.stdout
    checks = {
        "validate": True,
        "inventory": ctx.target_type in output,
        "lookup": ctx.target_title in output or ctx.target_id in output,
        "filter_type": (
            ctx.target_type in output or ctx.target_title in output or ctx.target_id in output
        ),
        "backlinks": _concept_id((int(ctx.target_id.rsplit("-", 1)[1]) - 1) % ctx.documents)
        in output,
        "graph": str(ctx.documents) in output
        if adapter.name in {"bash-python", "okf-parser"}
        else bool(output.strip()),
        "edit_validate": True,
    }
    return checks[task.name]


def _restore_title(ctx: TaskContext) -> None:
    text = ctx.target_path.read_text(encoding="utf-8")
    restored = text.replace("title: Updated Benchmark Title", f"title: {ctx.target_title}", 1)
    ctx.target_path.write_text(restored, encoding="utf-8")


def _failure(sample: Sample) -> dict[str, object]:
    return {
        "status": "failed",
        "returncode": sample.returncode,
        "stdout": sample.stdout[-2000:],
        "stderr": sample.stderr[-2000:],
    }


def _measure(
    adapter: Adapter, task: Task, ctx: TaskContext, config: RunConfig
) -> dict[str, object]:
    if not _available(adapter):
        return {"status": "unavailable"}
    invocation = _invocation(adapter, task, ctx)
    if invocation is None:
        return {"status": "unsupported"}

    for _ in range(config.warmups):
        if task.mutable:
            _restore_title(ctx)
        warm = _run(invocation)
        if warm.returncode != 0:
            return _failure(warm)

    samples: list[Sample] = []
    for _ in range(config.rounds):
        if task.mutable:
            _restore_title(ctx)
        sample = _run(invocation)
        if not _verify(task, adapter, sample, ctx):
            return _failure(sample)
        samples.append(sample)
    if task.mutable:
        _restore_title(ctx)

    elapsed = [sample.elapsed_ns for sample in samples]
    return {
        "status": "ok",
        "p50_ns": int(statistics.median(elapsed)),
        "p95_ns": _percentile(elapsed, 0.95),
        "stdout_bytes_p50": int(statistics.median(sample.stdout_bytes for sample in samples)),
        "stderr_bytes_p50": int(statistics.median(sample.stderr_bytes for sample in samples)),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=1_000)
    parser.add_argument("--body-bytes", type=int, default=512)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--target-index", type=int, default=137)
    parser.add_argument("--adapters", default="all", help="Comma-separated adapter names or 'all'.")
    parser.add_argument("--output", type=Path)
    return parser


def _validate_config(config: RunConfig) -> None:
    if config.documents > 0 and 0 <= config.target_index < config.documents:
        return
    message = "documents must be positive and target-index must be inside the corpus"
    raise ValueError(message)


def main() -> None:
    """Generate a corpus, run every supported agent task, and emit JSON."""
    args = _parser().parse_args()
    config = RunConfig(
        documents=args.documents,
        body_bytes=args.body_bytes,
        rounds=args.rounds,
        warmups=args.warmups,
        target_index=args.target_index,
    )
    _validate_config(config)

    adapters = _adapters()
    requested = {item.strip() for item in args.adapters.split(",") if item.strip()}
    if requested != {"all"}:
        adapters = tuple(adapter for adapter in adapters if adapter.name in requested)

    with tempfile.TemporaryDirectory(prefix="okf-agent-workload-") as temporary:
        root = Path(temporary) / "bundle"
        root.mkdir()
        _write_bundle(root, config.documents, config.body_bytes)
        ctx = _context(root, config)
        versions = {
            adapter.name: {
                "reported": _reported_version(adapter),
                "provenance": _provenance(adapter),
            }
            for adapter in adapters
        }
        results = [
            {"task": task.name, "adapter": adapter.name, **_measure(adapter, task, ctx, config)}
            for task in _TASKS
            for adapter in adapters
        ]

    payload = {
        "schema": "okf-agent-workload-v1",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "corpus": {
            "documents": config.documents,
            "body_bytes": config.body_bytes,
            "target_index": config.target_index,
            "common_subset": "OKF v0.1-compatible simple frontmatter + Markdown links",
        },
        "protocol": {
            "rounds": config.rounds,
            "warmups": config.warmups,
            "process_startup_included": True,
            "network_excluded_from_timing": True,
            "stdout_bytes_are_context_proxy": False,
            "stdout_bytes_role": "serialization diagnostic only",
        },
        "versions": versions,
        "tasks": [{"name": task.name, "description": task.description} for task in _TASKS],
        "results": results,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
