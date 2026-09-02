#!/usr/bin/env python3
"""Run one agentic OKF benchmark trial with an isolated tool and harness."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def load_json(name: str) -> dict[str, Any]:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def find_by_id(items: list[dict[str, Any]], identifier: str) -> dict[str, Any]:
    for item in items:
        if item["id"] == identifier:
            return item
    raise KeyError(identifier)


def document(name: str, concept_type: str, targets: list[str]) -> str:
    links = ""
    if targets:
        links = "links:\n" + "".join(f"  - concepts/{target}.md\n" for target in targets)
    body = "".join(f"- [{target}]({target}.md)\n" for target in targets)
    return (
        "---\n"
        f"type: {concept_type}\n"
        f"title: {name}\n"
        f"description: Fixture concept {name}\n"
        "generated:\n  by: agentic-capability\n  at: 2026-09-02T00:00:00Z\n"
        "sources:\n  - resource: agentic benchmark fixture\n"
        f"{links}"
        "---\n"
        f"\n# {name}\n\n{body}"
    )


def build_fixture(root: Path) -> None:
    concepts = {
        "a": ("Service", ["b"]),
        "b": ("Service", ["c"]),
        "c": ("Service", ["a"]),
        "d": ("Record", ["a"]),
        "e": ("Record", []),
        "f": ("Ledger", ["missing"]),
    }
    (root / "concepts").mkdir(parents=True)
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    for name, (concept_type, targets) in concepts.items():
        (root / "concepts" / f"{name}.md").write_text(
            document(name, concept_type, targets), encoding="utf-8"
        )
    types = root / "docs" / "types"
    types.mkdir(parents=True)
    for concept_type in ("Service", "Record", "Spec"):
        (types / f"{concept_type.lower()}.md").write_text(
            document(concept_type.lower(), "Spec", []), encoding="utf-8"
        )
    (root / "okf-base.yaml").write_text(
        'okf_version: "0.2"\nbase:\n  name: agentic-benchmark\n  roots:\n    - path: .\n'
        "  reserved_files:\n    index: index.md\n    log: log.md\n",
        encoding="utf-8",
    )


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def install_tool(tool: dict[str, Any], venv: Path) -> tuple[Path | None, str | None]:
    if tool["kind"] == "baseline":
        return None, None
    python = venv / "bin" / "python"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    spec = tool["package"]
    if tool.get("version"):
        spec += f"=={tool['version']}"
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", spec], check=True)
    executable = venv / "bin" / tool["executable"]
    return executable, str(spec)


def create_logged_shim(real_executable: Path, shim_dir: Path, log_path: Path) -> Path:
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / real_executable.name
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\t' \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" >> {log_path!s}\n"
        f"printf '%q ' \"$0\" \"$@\" >> {log_path!s}\n"
        f"printf '\\n' >> {log_path!s}\n"
        f"exec {real_executable!s} \"$@\"\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def prompt_for(task: dict[str, Any], tool: dict[str, Any], answer_file: Path) -> str:
    return (
        "You are participating in a controlled benchmark. Solve the task using the working "
        "directory exactly as provided.\n\n"
        f"MANDATED TOOL RULE: {tool['instruction']}\n"
        "You may inspect the mandated tool's --help and documentation available from the installed "
        "command. Do not substitute another OKF-specific package.\n\n"
        f"TASK: {task['prompt']}\n\n"
        f"Before finishing, write the final JSON object to {answer_file.name}. The grader reads that "
        "file. Do not modify the fixture to manufacture the expected result."
    )


def invoke_cline(workspace: Path, prompt: str, model: str, state: Path, env: dict[str, str],
                 timeout: int) -> subprocess.CompletedProcess[str]:
    auth = run(
        ["cline", "--data-dir", str(state), "auth", "--provider", "openrouter",
         "--apikey", env["OPENROUTER_API_KEY"], "--modelid", model],
        cwd=workspace,
        env=env,
        timeout=60,
    )
    if auth.returncode != 0:
        return auth
    return run(
        ["cline", "--data-dir", str(state), "-P", "openrouter", "-m", model,
         "--cwd", str(workspace), "--json", "--auto-approve", "true",
         "--timeout", str(timeout), prompt],
        cwd=workspace,
        env=env,
        timeout=timeout + 30,
    )


def invoke_kilo(workspace: Path, prompt: str, model: str, state: Path, env: dict[str, str],
                timeout: int) -> subprocess.CompletedProcess[str]:
    state.mkdir(parents=True, exist_ok=True)
    config = {
        "model": f"openrouter/{model}",
        "provider": {"openrouter": {"env": ["OPENROUTER_API_KEY"]}},
    }
    (workspace / "kilo.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return run(
        ["kilo", "run", "--auto", prompt],
        cwd=workspace,
        env=env,
        timeout=timeout + 30,
    )


def normalize(value: Any) -> Any:
    if isinstance(value, list):
        if value and all(isinstance(item, list) for item in value):
            return sorted(sorted(item) for item in value)
        return sorted(value) if all(isinstance(item, str) for item in value) else value
    if isinstance(value, dict):
        return {key: value[key] for key in sorted(value)}
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--harness", required=True, choices=["cline", "kilo"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--repetition", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is required")

    task = find_by_id(load_json("tasks.json")["tasks"], args.task)
    tool = find_by_id(load_json("tools.json")["tools"], args.tool)
    harness = find_by_id(load_json("harnesses.json")["harnesses"], args.harness)

    run_id = str(uuid.uuid4())
    started = datetime.now(UTC)
    started_clock = time.monotonic()
    status = "agent_error"
    failure_class: str | None = None
    graded = False
    produced: Any = None
    tool_used = tool["kind"] == "baseline"
    transcript = ""
    exit_status: int | None = None
    install_spec: str | None = None

    with tempfile.TemporaryDirectory(prefix="okf-agentic-") as temp:
        root = Path(temp)
        workspace = root / "workspace"
        fixture = workspace / "bundle"
        workspace.mkdir()
        build_fixture(fixture)
        answer_file = workspace / "answer.json"
        state = root / f"{args.harness}-state"
        venv = root / "tool-venv"
        tool_log = root / "tool-invocations.log"
        shim_dir = root / "shim"
        env = os.environ.copy()
        env["CI"] = "true"
        env["NO_COLOR"] = "1"

        try:
            real_executable, install_spec = install_tool(tool, venv)
            if real_executable is not None:
                create_logged_shim(real_executable, shim_dir, tool_log)
                env["PATH"] = f"{shim_dir}:{venv / 'bin'}:{env['PATH']}"
            prompt = prompt_for(task, tool, answer_file)
            if args.harness == "cline":
                completed = invoke_cline(workspace, prompt, args.model, state, env, args.timeout)
            else:
                completed = invoke_kilo(workspace, prompt, args.model, state, env, args.timeout)
            exit_status = completed.returncode
            transcript = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
            if tool_log.exists():
                tool_used = bool(tool_log.read_text(encoding="utf-8").strip())
            if answer_file.exists():
                answer = json.loads(answer_file.read_text(encoding="utf-8"))
                produced = answer.get("answer")
                graded = normalize(produced) == normalize(task["expected"])
                if not tool_used:
                    status, failure_class = "failure", "mandated_tool_not_used"
                elif graded:
                    status = "success"
                else:
                    status, failure_class = "failure", "wrong_answer"
            elif exit_status != 0:
                status, failure_class = "failure", "harness_or_agent_error"
            else:
                status, failure_class = "failure", "missing_answer"
        except subprocess.TimeoutExpired as exc:
            transcript = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + (
                (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            )
            status, failure_class = "failure", "timeout"
        except Exception as exc:  # benchmark records infrastructure failure explicitly
            transcript += f"\nrunner exception: {type(exc).__name__}: {exc}\n"
            status, failure_class = "failure", "harness_error"

        args.output.parent.mkdir(parents=True, exist_ok=True)
        transcript_path = args.output.with_suffix(".transcript.txt")
        transcript_path.write_text(transcript, encoding="utf-8")
        tool_log_text = tool_log.read_text(encoding="utf-8") if tool_log.exists() else ""
        tool_log_path = args.output.with_suffix(".tool.log")
        tool_log_path.write_text(tool_log_text, encoding="utf-8")

    finished = datetime.now(UTC)
    record = {
        "schema_version": 1,
        "run_id": run_id,
        "task_id": task["id"],
        "tool": {
            "id": tool["id"],
            "kind": tool["kind"],
            "package": tool.get("package"),
            "version": tool.get("version"),
            "install_spec": install_spec,
            "tool_used": tool_used,
        },
        "agent": {
            "harness": harness["id"],
            "harness_package": harness["package"],
            "model": args.model,
            "provider": "openrouter",
        },
        "budget": {"wall_seconds": args.timeout},
        "repetition": args.repetition,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "wall_seconds": round(time.monotonic() - started_clock, 3),
        "result": {
            "status": status,
            "graded": graded,
            "failure_class": failure_class,
            "produced": produced,
            "expected": task["expected"],
            "exit_status": exit_status,
        },
        "artifacts": {
            "transcript": args.output.with_suffix(".transcript.txt").name,
            "tool_log": args.output.with_suffix(".tool.log").name,
        },
    }
    args.output.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
