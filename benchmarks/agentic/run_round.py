#!/usr/bin/env python3
# ruff: noqa
"""Run the first large-scale agentic OKF benchmark round serially by tool."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

import yaml
from okf_parser import load_bundle, validate_path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASELINE_ID = "baseline-none"


def concept_rows(path: Path, concept_type: str) -> list[dict[str, Any]]:
    """Read authored benchmark registries through okf-parser itself."""
    frame = load_bundle(path).concepts.execute()
    rows: list[dict[str, Any]] = []
    for row in frame.itertuples():
        data = json.loads(row.frontmatter_json)
        if data.get("type") == concept_type:
            rows.append(data)
    return rows


def boolish(value: object) -> bool:
    return value is True or str(value).strip().lower() == "true"


def tasks() -> list[dict[str, Any]]:
    return sorted(concept_rows(HERE / "tasks", "BenchmarkTask"), key=lambda x: x["task_id"])


def enabled_rivals() -> list[dict[str, Any]]:
    rows = [
        row
        for row in concept_rows(REPO / "benchmarks" / "rivals", "Rival")
        if boolish(row.get("agentic_enabled"))
    ]
    return sorted(rows, key=lambda x: (x["title"] != "okf-parser", x["title"]))


def enabled_harness() -> dict[str, Any]:
    rows = [
        row
        for row in concept_rows(HERE / "harnesses", "BenchmarkHarness")
        if boolish(row.get("enabled"))
    ]
    if len(rows) != 1:
        raise RuntimeError(f"first round requires exactly one enabled harness, found {len(rows)}")
    if rows[0]["harness_id"] != "cline":
        raise RuntimeError("first round is intentionally fixed to the Cline harness")
    return rows[0]


def select(raw: str, available: list[str]) -> list[str]:
    if raw == "all":
        return available
    selected = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise ValueError(f"unknown selection {unknown}; available={available}")
    return selected


def document(name: str, concept_type: str, targets: list[str]) -> str:
    front = {
        "type": concept_type,
        "title": name,
        "description": f"Deterministic agentic benchmark concept {name}",
    }
    body = "".join(f"- [{Path(target).stem}]({target})\n" for target in targets)
    return f"---\n{yaml.safe_dump(front, sort_keys=False)}---\n\n# {name}\n\n{body}"


def build_mixed_bundle(root: Path, size: int) -> dict[str, str]:
    """Build a large heterogeneous graph and return hidden canonical oracle answers."""
    concepts = root / "concepts"
    concepts.mkdir(parents=True)
    (root / "index.md").write_text("# Large mixed benchmark bundle\n", encoding="utf-8")

    domain_types = ["Service", "Record", "Ledger", "Policy", "Decision", "Source"]
    counts: Counter[str] = Counter()
    for i in range(size):
        name = f"n{i:04d}"
        concept_type = domain_types[i % len(domain_types)]
        counts[concept_type] += 1
        targets = [f"n{i - 1:04d}.md"] if i else []
        (concepts / f"{name}.md").write_text(
            document(name, concept_type, targets), encoding="utf-8"
        )

    cycle_lines: list[str] = []
    for group in range(10):
        names = [f"cycle{group:02d}-{offset}" for offset in range(3)]
        cycle_lines.append(",".join(names))
        for offset, name in enumerate(names):
            concept_type = domain_types[(group + offset) % len(domain_types)]
            counts[concept_type] += 1
            target = f"{names[(offset + 1) % len(names)]}.md"
            (concepts / f"{name}.md").write_text(
                document(name, concept_type, [target]), encoding="utf-8"
            )

    orphan_names = [f"orphan-{i:03d}" for i in range(24)]
    for i, name in enumerate(orphan_names):
        concept_type = domain_types[i % len(domain_types)]
        counts[concept_type] += 1
        (concepts / f"{name}.md").write_text(
            document(name, concept_type, []), encoding="utf-8"
        )

    broken_names = [f"broken-{i:03d}" for i in range(12)]
    for i, name in enumerate(broken_names):
        concept_type = domain_types[(i + 2) % len(domain_types)]
        counts[concept_type] += 1
        (concepts / f"{name}.md").write_text(
            document(name, concept_type, [f"missing-{i:03d}.md"]), encoding="utf-8"
        )

    spec_types = ["Service", "Record", "Policy", "Decision", "Source", "Spec"]
    spec_dir = root / "docs" / "types"
    spec_dir.mkdir(parents=True)
    for concept_type in spec_types:
        slug = concept_type.lower()
        counts["Spec"] += 1
        (spec_dir / f"{slug}.md").write_text(document(slug, "Spec", []), encoding="utf-8")

    total = sum(counts.values())
    no_inbound = [f"n{size - 1:04d}", *orphan_names, *broken_names, *[x.lower() for x in spec_types]]
    target = f"n{size // 2:04d}"
    impact = [f"n{i:04d}" for i in range(size // 2 + 1, size)]
    (root.parent / "target.txt").write_text(target + "\n", encoding="utf-8")

    report = validate_path(root)
    return {
        "conformant": "true" if report.is_conformant else "false",
        "concept-count": str(total),
        "type-counts": "\n".join(f"{name}={counts[name]}" for name in sorted(counts)),
        "no-inbound": "\n".join(sorted(no_inbound)),
        "cycles": "\n".join(sorted(cycle_lines)),
        "unresolved-links": "12",
        "types-without-spec": "Ledger",
        "impact-delete-middle": "\n".join(sorted(impact)),
    }


def build_json_records(workspace: Path, size: int) -> list[dict[str, str]]:
    records = [
        {
            "record_id": f"rec-{i:05d}",
            "name": f"Record {i:05d}",
            "category": ("alpha", "beta", "gamma", "delta")[i % 4],
            "score": str((i * 7) % 1009),
            "active": "yes" if i % 5 else "no",
            "source": f"source-{i % 17:02d}",
        }
        for i in range(size)
    ]
    (workspace / "input.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return records


def install_tool(tool: dict[str, Any], root: Path) -> tuple[Path | None, float]:
    if tool["title"] == BASELINE_ID:
        return None, 0.0
    started = time.monotonic()
    venv = root / "tool-venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv / "bin" / "python"
    spec = f"{tool['package']}=={tool['agentic_version']}"
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", spec],
        check=True,
        capture_output=True,
        text=True,
    )
    executable = venv / "bin" / str(tool["agentic_executable"])
    if not executable.exists():
        raise FileNotFoundError(f"{spec} did not install {tool['agentic_executable']}")
    return executable, time.monotonic() - started


def create_tool_shim(real: Path, root: Path) -> tuple[Path, Path]:
    log = root / "tool.log"
    shim_dir = root / "shim"
    shim_dir.mkdir()
    shim = shim_dir / real.name
    shim.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"printf '%s\\t' \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" >> {log}\n"
        f"printf '%q ' \"$0\" \"$@\" >> {log}\n"
        f"printf '\\n' >> {log}\n"
        f"exec {real} \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim_dir, log


def cline_command(
    prompt: str,
    workspace: Path,
    state: Path,
    model: str,
    key: str,
    timeout: int,
) -> list[str]:
    cline = shutil.which("cline")
    if cline is None:
        raise FileNotFoundError("cline executable is not installed")
    return [
        cline,
        "--json",
        "--verbose",
        "--yolo",
        "--data-dir",
        str(state),
        "-P",
        "openrouter",
        "-m",
        model,
        "-k",
        key,
        "-t",
        str(timeout),
        "-c",
        str(workspace),
        prompt,
    ]


def extract_usage(transcript: str) -> tuple[int | None, int | None, int | None, float | None]:
    """Extract only explicit usage reported by Cline; never estimate missing values."""
    input_values: list[int] = []
    output_values: list[int] = []
    total_values: list[int] = []
    cost_values: list[float] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = re.sub(r"[^a-z]", "", key.lower())
                if isinstance(child, (int, float)):
                    if normalized in {"inputtokens", "prompttokens"}:
                        input_values.append(int(child))
                    elif normalized in {"outputtokens", "completiontokens"}:
                        output_values.append(int(child))
                    elif normalized == "totaltokens":
                        total_values.append(int(child))
                    elif normalized in {"cost", "costusd", "totalcost", "estimatedcost"}:
                        cost_values.append(float(child))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for line in transcript.splitlines():
        try:
            visit(json.loads(line))
        except json.JSONDecodeError:
            continue

    input_tokens = max(input_values) if input_values else None
    output_tokens = max(output_values) if output_values else None
    total_tokens = max(total_values) if total_values else None
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    cost = max(cost_values) if cost_values else None
    return input_tokens, output_tokens, total_tokens, cost


def canonical_lines(text: str) -> str:
    return "\n".join(sorted(line.strip() for line in text.splitlines() if line.strip()))


def grade_conversion(workspace: Path, source: list[dict[str, str]]) -> tuple[bool, str, str]:
    converted = workspace / "converted"
    expected = f"{len(source)} records preserved as {len(source)} OKF concepts"
    if not converted.is_dir():
        return False, "converted/ directory missing", expected
    report = validate_path(converted)
    if not report.is_conformant:
        return False, "converted bundle is not conformant", expected
    frame = load_bundle(converted).concepts.execute()
    if len(frame) != len(source):
        return False, f"expected {len(source)} concepts, found {len(frame)}", expected
    by_id: dict[str, dict[str, Any]] = {}
    for row in frame.itertuples():
        data = json.loads(row.frontmatter_json)
        record_id = str(data.get("record_id", ""))
        if record_id:
            by_id[record_id] = data
    for record in source:
        actual = by_id.get(record["record_id"])
        if actual is None:
            return False, f"missing {record['record_id']}", expected
        for key, value in record.items():
            if str(actual.get(key, "")) != value:
                return False, f"{record['record_id']} lost or changed {key}", expected
    return True, expected, expected


def grade_task(
    task: dict[str, Any], workspace: Path, oracle: dict[str, str], source: list[dict[str, str]] | None
) -> tuple[bool, str, str]:
    task_id = str(task["task_id"])
    if task["grader"] == "json-to-okf":
        assert source is not None
        return grade_conversion(workspace, source)
    expected = oracle[task_id]
    answer = workspace / "answer.txt"
    if not answer.is_file():
        return False, "answer.txt missing", expected
    produced = answer.read_text(encoding="utf-8").strip()
    if task["answer_kind"] == "lines":
        produced = canonical_lines(produced)
        expected = canonical_lines(expected)
    return produced == expected, produced, expected


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_run(
    bundle: Path,
    raw_dir: Path,
    *,
    task: dict[str, Any],
    tool: dict[str, Any],
    harness: dict[str, Any],
    model: str,
    repetition: int,
    timeout: int,
    setup_seconds: float,
    wall_seconds: float,
    started: datetime,
    finished: datetime,
    tool_used: bool,
    graded: bool,
    produced: str,
    expected: str,
    failure_class: str | None,
    usage: tuple[int | None, int | None, int | None, float | None],
    transcript: str,
    tool_log: str,
) -> Path:
    run_id = str(uuid.uuid4())
    raw_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = raw_dir / f"{run_id}.transcript.txt"
    tool_log_path = raw_dir / f"{run_id}.tool.txt"
    answer_path = raw_dir / f"{run_id}.answer.txt"
    transcript_path.write_text(transcript, encoding="utf-8")
    tool_log_path.write_text(tool_log, encoding="utf-8")
    answer_path.write_text(produced, encoding="utf-8")
    input_tokens, output_tokens, total_tokens, cost = usage
    data = {
        "type": "BenchmarkRun",
        "title": f"{tool['title']} / {task['task_id']} / repetition {repetition}",
        "description": "Immutable evidence from one agentic benchmark trial",
        "run_id": run_id,
        "task_id": task["task_id"],
        "tool_id": tool["title"],
        "tool_package": tool.get("package") or "",
        "tool_version": tool.get("agentic_version") or "",
        "tool_executable": tool.get("agentic_executable") or "",
        "tool_used": tool_used,
        "harness_id": harness["harness_id"],
        "harness_version": harness["version"],
        "model": model,
        "provider": harness["provider"],
        "repetition": repetition,
        "setup_seconds": round(setup_seconds, 3),
        "wall_seconds": round(wall_seconds, 3),
        "budget_seconds": timeout,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost,
        "usage_available": total_tokens is not None or input_tokens is not None or output_tokens is not None,
        "status": "success" if graded and tool_used else "failure",
        "graded": graded,
        "failure_class": failure_class or "",
        "answer_sha256": sha(produced),
        "expected_sha256": sha(expected),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "answer_path": str(answer_path.relative_to(bundle.parent)),
        "transcript_path": str(transcript_path.relative_to(bundle.parent)),
        "tool_log_path": str(tool_log_path.relative_to(bundle.parent)),
    }
    runs = bundle / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{run_id}.md"
    body = (
        f"# {data['title']}\n\n"
        f"Status: **{data['status']}**. Grader: **{'pass' if graded else 'fail'}**. "
        f"Mandated tool observed: **{tool_used}**.\n"
    )
    path.write_text(
        f"---\n{yaml.safe_dump(data, sort_keys=False, allow_unicode=True)}---\n\n{body}",
        encoding="utf-8",
    )
    return path


def build_summary(bundle: Path, destination: Path) -> None:
    frame = load_bundle(bundle).concepts.execute()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in frame.itertuples():
        data = json.loads(row.frontmatter_json)
        if data.get("type") == "BenchmarkRun":
            groups[str(data["tool_id"])].append(data)
    lines = [
        "# Agentic benchmark round",
        "",
        "Cline is fixed for this round. Tools run serially, baseline first, and each tool completes all tasks before the next starts.",
        "",
        "| tool | passes | trials | pass rate | median seconds (pass) | median tokens (pass) | median cost USD (pass) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for tool, rows in groups.items():
        passed = [row for row in rows if boolish(row.get("graded")) and row.get("status") == "success"]
        seconds = [float(row["wall_seconds"]) for row in passed]
        tokens = [int(row["total_tokens"]) for row in passed if row.get("total_tokens") not in (None, "")]
        costs = [float(row["cost_usd"]) for row in passed if row.get("cost_usd") not in (None, "")]
        lines.append(
            f"| {tool} | {len(passed)} | {len(rows)} | {len(passed)/len(rows):.0%} | "
            f"{median(seconds):.2f if seconds else '—'} | "
            f"{int(median(tokens)) if tokens else '—'} | "
            f"{median(costs):.4f if costs else '—'} |"
        )
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="all")
    parser.add_argument("--tools", default="all")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 3:
        raise SystemExit("repetitions must be between 1 and 3")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is required")

    task_rows = tasks()
    task_ids = [str(row["task_id"]) for row in task_rows]
    chosen_task_ids = select(args.tasks, task_ids)
    chosen_tasks = [row for row in task_rows if row["task_id"] in chosen_task_ids]

    rivals = enabled_rivals()
    available_tools = [BASELINE_ID, *[str(row["title"]) for row in rivals]]
    chosen_tool_ids = select(args.tools, available_tools)
    if BASELINE_ID in chosen_tool_ids:
        chosen_tool_ids = [BASELINE_ID, *[x for x in chosen_tool_ids if x != BASELINE_ID]]
    tools_by_id = {str(row["title"]): row for row in rivals}
    tools_by_id[BASELINE_ID] = {
        "title": BASELINE_ID,
        "package": None,
        "agentic_version": None,
        "agentic_executable": None,
        "agentic_instruction": (
            "Do not install or invoke any OKF-specific tool. Use only ordinary shell utilities and standard-library scripting."
        ),
    }
    harness = enabled_harness()

    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    bundle = output / "okf"
    raw = output / "raw"
    (bundle / "docs" / "types").mkdir(parents=True)
    shutil.copy2(REPO / "docs" / "types" / "benchmarkrun.md", bundle / "docs" / "types" / "benchmarkrun.md")
    shutil.copy2(
        REPO / "docs" / "types" / "benchmarkrun.schema.sql",
        bundle / "docs" / "types" / "benchmarkrun.schema.sql",
    )

    cline = shutil.which("cline")
    if cline is None:
        raise SystemExit("Cline is not installed")
    base_path = os.pathsep.join(dict.fromkeys([str(Path(cline).parent), "/usr/local/bin", "/usr/bin", "/bin"]))

    for tool_id in chosen_tool_ids:
        tool = tools_by_id[tool_id]
        print(f"=== TOOL {tool_id}: all tasks before advancing ===", flush=True)
        for task in chosen_tasks:
            for repetition in range(1, args.repetitions + 1):
                with tempfile.TemporaryDirectory(prefix="okf-agentic-") as temp:
                    root = Path(temp)
                    workspace = root / "workspace"
                    workspace.mkdir()
                    source: list[dict[str, str]] | None = None
                    oracle: dict[str, str] = {}
                    if task["fixture_kind"] == "large-mixed-bundle":
                        oracle = build_mixed_bundle(workspace / "bundle", int(task["fixture_size"]))
                    elif task["fixture_kind"] == "json-records":
                        source = build_json_records(workspace, int(task["fixture_size"]))
                    else:
                        raise RuntimeError(f"unknown fixture {task['fixture_kind']}")

                    setup_started = time.monotonic()
                    failure_class: str | None = None
                    try:
                        executable, setup_seconds = install_tool(tool, root)
                    except Exception as exc:
                        executable = None
                        setup_seconds = time.monotonic() - setup_started
                        failure_class = "setup_error"
                        transcript = f"setup error: {type(exc).__name__}: {exc}\n"
                        started = finished = datetime.now(UTC)
                        usage = (None, None, None, None)
                        graded = False
                        produced = transcript.strip()
                        expected = "trial setup succeeds"
                        tool_used = False
                        tool_log_text = ""
                    else:
                        env = os.environ.copy()
                        env["PATH"] = base_path
                        tool_log = root / "tool.log"
                        if executable is not None:
                            shim_dir, tool_log = create_tool_shim(executable, root)
                            env["PATH"] = f"{shim_dir}:{executable.parent}:{base_path}"
                        if tool_id == "okf-nav":
                            env["OKF_BUNDLES_DIR"] = str(workspace)
                        prompt = (
                            "You are running a controlled benchmark. Work only in the provided workspace. "
                            f"{tool['agentic_instruction']} Do not substitute another OKF-specific package. "
                            "For a non-baseline condition, the mandated executable must materially contribute. "
                            f"Task: {task['prompt']} Do not ask follow-up questions."
                        )
                        state = root / "cline-state"
                        started = datetime.now(UTC)
                        tick = time.monotonic()
                        try:
                            completed = subprocess.run(
                                cline_command(prompt, workspace, state, args.model, key, args.timeout),
                                cwd=workspace,
                                env=env,
                                text=True,
                                capture_output=True,
                                check=False,
                                timeout=args.timeout + 30,
                            )
                            transcript = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
                        except subprocess.TimeoutExpired as exc:
                            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
                            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
                            transcript = stdout + stderr
                            failure_class = "timeout"
                        wall_seconds = time.monotonic() - tick
                        finished = datetime.now(UTC)
                        usage = extract_usage(transcript)
                        tool_log_text = tool_log.read_text(encoding="utf-8") if tool_log.exists() else ""
                        tool_used = tool_id == BASELINE_ID or bool(tool_log_text.strip())
                        graded, produced, expected = grade_task(task, workspace, oracle, source)
                        if not tool_used:
                            failure_class = "mandated_tool_not_used"
                        elif not graded and failure_class is None:
                            failure_class = "wrong_answer"
                        setup_seconds = setup_seconds

                    if failure_class == "setup_error":
                        wall_seconds = 0.0
                    write_run(
                        bundle,
                        raw,
                        task=task,
                        tool=tool,
                        harness=harness,
                        model=args.model,
                        repetition=repetition,
                        timeout=args.timeout,
                        setup_seconds=setup_seconds,
                        wall_seconds=wall_seconds,
                        started=started,
                        finished=finished,
                        tool_used=tool_used,
                        graded=graded,
                        produced=produced,
                        expected=expected,
                        failure_class=failure_class,
                        usage=usage,
                        transcript=transcript,
                        tool_log=tool_log_text,
                    )

    build_summary(bundle, output / "summary.md")
    print(f"Results written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
