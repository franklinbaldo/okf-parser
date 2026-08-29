# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "litellm>=1.98,<2",
#     "okf-parser",
#     "tiktoken>=0.14,<0.15",
# ]
#
# [tool.uv.sources]
# okf-parser = { path = "..", editable = true }
# ///
"""Measure model-selected knowledge access through provider-neutral LiteLLM routing."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from litellm import completion

from okf_parser.engine import load_bundle

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from okf_parser.bundle import Bundle

_BENCHMARK_DIR = Path(__file__).resolve().parent
if str(_BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARK_DIR))

import agent_token_cost as trace  # noqa: E402
import agent_workload as workload  # noqa: E402
import generic_agent_baseline as generic  # noqa: E402

_STRATEGIES = ("direct-markdown", "generic-retrieval", "okf-parser")
_MAX_MODEL_CALLS = 10
_MAX_TOOL_ITEMS = 20


@dataclass(frozen=True, slots=True)
class Usage:
    """Cumulative provider-reported usage normalized by LiteLLM."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0

    def add(self, response: Any) -> Usage:  # noqa: ANN401
        """Return usage with one LiteLLM completion call accumulated."""
        usage = response.usage
        if usage is None:
            message = "LiteLLM response did not include provider token usage"
            raise RuntimeError(message)
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            message = "provider usage must include integer prompt/completion tokens"
            raise RuntimeError(message)
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens
        if not isinstance(total_tokens, int):
            message = "provider usage total_tokens must be an integer"
            raise RuntimeError(message)
        return Usage(
            input_tokens=self.input_tokens + input_tokens,
            output_tokens=self.output_tokens + output_tokens,
            total_tokens=self.total_tokens + total_tokens,
            calls=self.calls + 1,
        )


ToolResult = tuple[dict[str, object], int, int]


@dataclass(frozen=True, slots=True)
class ToolRuntime:
    """Tool definitions and executor exposed to one access strategy."""

    definitions: list[dict[str, object]]
    execute: Callable[[str, dict[str, object]], ToolResult]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _function_tool(
    name: str,
    description: str,
    properties: dict[str, object],
    required: Sequence[str],
) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def _page(values: Sequence[str], *, offset: int, limit: int) -> tuple[list[str], int]:
    bounded_limit = max(1, min(limit, _MAX_TOOL_ITEMS))
    bounded_offset = max(0, offset)
    return list(values[bounded_offset : bounded_offset + bounded_limit]), len(values)


def _generic_runtime(root: Path) -> ToolRuntime:
    definitions = [
        _function_tool(
            "read_document",
            "Read one authored Markdown document by concept id.",
            {"concept_id": {"type": "string"}},
            ["concept_id"],
        ),
        _function_tool(
            "search_text",
            (
                "Search the authored Markdown corpus for a literal string. Return matching "
                "concept ids, compact matching lines, and the exact number of documents "
                "containing the query."
            ),
            {
                "query": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            ["query", "offset", "limit"],
        ),
    ]

    def execute(name: str, arguments: dict[str, object]) -> ToolResult:
        started = time.perf_counter_ns()
        if name == "read_document":
            concept_id = str(arguments["concept_id"])
            payload = generic.show(root, concept_id)
            return (
                {"concept_id": concept_id, "markdown": payload["text"]},
                1,
                time.perf_counter_ns() - started,
            )
        if name != "search_text":
            message = f"unknown generic tool: {name}"
            raise ValueError(message)

        query = str(arguments["query"])
        offset = int(arguments["offset"])
        limit = int(arguments["limit"])
        matches: list[tuple[str, list[str]]] = []
        for path in generic._documents(root):  # noqa: SLF001
            text = path.read_text(encoding="utf-8")
            if query not in text:
                continue
            lines = [line.strip() for line in text.splitlines() if query in line]
            matches.append(
                (
                    generic._concept_id(root, path),  # noqa: SLF001
                    lines[:_MAX_TOOL_ITEMS],
                )
            )
        ids, total = _page([item[0] for item in matches], offset=offset, limit=limit)
        selected = dict(matches)
        return (
            {
                "query": query,
                "matches": [
                    {"concept_id": concept_id, "lines": selected[concept_id]} for concept_id in ids
                ],
                "total_matches": total,
                "offset": max(0, offset),
            },
            len(ids),
            time.perf_counter_ns() - started,
        )

    return ToolRuntime(definitions, execute)


def _okf_runtime(bundle: Bundle) -> ToolRuntime:
    concepts = cast(
        "list[dict[str, object]]",
        bundle.concepts.execute().to_dict(orient="records"),
    )
    links = cast(
        "list[dict[str, object]]",
        bundle.links.execute().to_dict(orient="records"),
    )
    by_id = {str(row["concept_id"]): row for row in concepts}
    definitions = [
        _function_tool(
            "get_concept",
            (
                "Get one canonical OKF concept with title, type, body, and resolved outgoing "
                "relation targets."
            ),
            {"concept_id": {"type": "string"}},
            ["concept_id"],
        ),
        _function_tool(
            "filter_concepts",
            "Filter canonical concepts by exact type and return a page plus exact total count.",
            {
                "concept_type": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            ["concept_type", "offset", "limit"],
        ),
        _function_tool(
            "incoming_links",
            "Return canonical concepts that link to a target concept id.",
            {
                "concept_id": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            ["concept_id", "offset", "limit"],
        ),
    ]

    def execute(name: str, arguments: dict[str, object]) -> ToolResult:
        started = time.perf_counter_ns()
        if name == "get_concept":
            concept_id = str(arguments["concept_id"])
            row = by_id.get(concept_id)
            if row is None:
                return (
                    {"error": "not_found", "concept_id": concept_id},
                    0,
                    time.perf_counter_ns() - started,
                )
            targets = sorted(
                str(link["target_id"])
                for link in links
                if link.get("source_id") == concept_id and link.get("target_id") is not None
            )
            return (
                {
                    "concept_id": concept_id,
                    "title": row.get("title"),
                    "type": row.get("concept_type"),
                    "body": row.get("body"),
                    "outgoing": targets,
                },
                1,
                time.perf_counter_ns() - started,
            )

        offset = int(arguments["offset"])
        limit = int(arguments["limit"])
        if name == "filter_concepts":
            concept_type = str(arguments["concept_type"])
            ids = sorted(
                str(row["concept_id"])
                for row in concepts
                if row.get("concept_type") == concept_type
            )
            page, total = _page(ids, offset=offset, limit=limit)
            return (
                {
                    "concept_type": concept_type,
                    "concept_ids": page,
                    "total_matches": total,
                    "offset": max(0, offset),
                },
                len(page),
                time.perf_counter_ns() - started,
            )
        if name == "incoming_links":
            concept_id = str(arguments["concept_id"])
            ids = sorted(
                str(link["source_id"])
                for link in links
                if link.get("target_id") == concept_id and link.get("source_id") is not None
            )
            page, total = _page(ids, offset=offset, limit=limit)
            return (
                {
                    "concept_id": concept_id,
                    "source_ids": page,
                    "total_matches": total,
                    "offset": max(0, offset),
                },
                len(page),
                time.perf_counter_ns() - started,
            )

        message = f"unknown OKF tool: {name}"
        raise ValueError(message)

    return ToolRuntime(definitions, execute)


def _task_prompt(task: trace.KnowledgeTask, strategy: str) -> str:
    shape = {key: type(value).__name__ for key, value in task.expected.items()}
    return (
        "Solve the task using only the knowledge access provided in this run. "
        "Use tools when available. Do not guess. "
        "Return exactly one compact JSON object with no Markdown fences or extra prose.\n\n"
        f"Task: {task.prompt}\n"
        f"Strategy label: {strategy}\n"
        f"Required JSON shape: {_json(shape)}"
    )


def _parse_answer(text: object) -> dict[str, object] | None:
    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _correct(task: trace.KnowledgeTask, answer: dict[str, object] | None) -> bool:
    return answer is not None and trace._normalized(answer) == trace._normalized(  # noqa: SLF001
        task.expected
    )


def _assistant_message(message: Any) -> dict[str, object]:  # noqa: ANN401
    """Convert LiteLLM's normalized assistant message into reusable history."""
    payload = message.model_dump(exclude_none=True)
    if not isinstance(payload, dict):
        error = "LiteLLM assistant message did not serialize to an object"
        raise TypeError(error)
    return cast("dict[str, object]", payload)


def _run_direct(
    *,
    model: str,
    task: trace.KnowledgeTask,
    markdown: str,
    documents: int,
) -> dict[str, object]:
    started = time.perf_counter_ns()
    response = completion(
        model=model,
        messages=[
            {
                "role": "user",
                "content": (
                    _task_prompt(task, "direct-markdown")
                    + "\n\nAuthored Markdown corpus:\n"
                    + markdown
                ),
            }
        ],
        num_retries=0,
    )
    usage = Usage().add(response)
    answer = _parse_answer(response.choices[0].message.content)
    success = _correct(task, answer)
    return {
        "status": "ok" if success else "wrong_answer",
        "success": success,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "calls": usage.calls,
        "tool_calls": 0,
        "retrieved_items": documents,
        "tool_latency_ns": 0,
        "latency_ns": time.perf_counter_ns() - started,
        "answer": answer,
    }


def _run_tools(  # noqa: PLR0913
    *,
    model: str,
    task: trace.KnowledgeTask,
    strategy: str,
    runtime: ToolRuntime,
    max_calls: int,
) -> dict[str, object]:
    started = time.perf_counter_ns()
    messages: list[dict[str, object]] = [{"role": "user", "content": _task_prompt(task, strategy)}]
    usage = Usage()
    tool_calls = 0
    retrieved_items = 0
    tool_latency_ns = 0
    answer: dict[str, object] | None = None

    while usage.calls < max_calls:
        response = completion(
            model=model,
            messages=messages,
            tools=runtime.definitions,
            num_retries=0,
        )
        usage = usage.add(response)
        message = response.choices[0].message
        calls = message.tool_calls or []
        messages.append(_assistant_message(message))
        if not calls:
            answer = _parse_answer(message.content)
            break

        for call in calls:
            call_id = call.id
            name = call.function.name
            raw_arguments = call.function.arguments
            if not isinstance(call_id, str) or not isinstance(name, str):
                error = "LiteLLM tool call must contain string id and function name"
                raise TypeError(error)
            if not isinstance(raw_arguments, str):
                error = "LiteLLM tool-call arguments must be a JSON string"
                raise TypeError(error)
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                error = "tool arguments must decode to an object"
                raise TypeError(error)
            output, item_count, elapsed = runtime.execute(name, arguments)
            retrieved_items += item_count
            tool_calls += 1
            tool_latency_ns += elapsed
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _json(output),
                }
            )

    success = _correct(task, answer)
    status = "ok" if success else ("max_calls" if usage.calls >= max_calls else "wrong_answer")
    return {
        "status": status,
        "success": success,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "calls": usage.calls,
        "tool_calls": tool_calls,
        "retrieved_items": retrieved_items,
        "tool_latency_ns": tool_latency_ns,
        "latency_ns": time.perf_counter_ns() - started,
        "answer": answer,
    }


def _percentile(values: Sequence[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _aggregate(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    successful = [sample for sample in samples if sample["success"] is True]
    inputs = [int(sample["input_tokens"]) for sample in samples]
    outputs = [int(sample["output_tokens"]) for sample in samples]
    totals = [int(sample["total_tokens"]) for sample in samples]
    return {
        "runs": len(samples),
        "successes": len(successful),
        "success_rate": len(successful) / len(samples) if samples else 0.0,
        "input_tokens_mean": statistics.fmean(inputs) if inputs else 0.0,
        "input_tokens_p50": int(statistics.median(inputs)) if inputs else 0,
        "input_tokens_p95": _percentile(inputs, 0.95),
        "output_tokens_mean": statistics.fmean(outputs) if outputs else 0.0,
        "total_tokens_mean": statistics.fmean(totals) if totals else 0.0,
        "calls_mean": (
            statistics.fmean(int(sample["calls"]) for sample in samples) if samples else 0.0
        ),
        "tool_calls_mean": (
            statistics.fmean(int(sample["tool_calls"]) for sample in samples) if samples else 0.0
        ),
        "retrieved_items_mean": (
            statistics.fmean(int(sample["retrieved_items"]) for sample in samples)
            if samples
            else 0.0
        ),
        "tool_latency_ns_mean": (
            statistics.fmean(int(sample["tool_latency_ns"]) for sample in samples)
            if samples
            else 0.0
        ),
        "latency_ns_mean": (
            statistics.fmean(int(sample["latency_ns"]) for sample in samples) if samples else 0.0
        ),
        "tokens_per_success": sum(inputs) / len(successful) if successful else None,
    }


def _add_comparisons(results: list[dict[str, object]]) -> None:
    by_task: dict[str, dict[str, int]] = {}
    for result in results:
        by_task.setdefault(str(result["task"]), {})[str(result["strategy"])] = int(
            result["input_tokens_p50"]
        )

    for result in results:
        task_baselines = by_task[str(result["task"])]
        current = int(result["input_tokens_p50"])
        for label, strategy in (
            ("direct", "direct-markdown"),
            ("generic", "generic-retrieval"),
        ):
            baseline = task_baselines.get(strategy)
            if baseline is None:
                continue
            result[f"input_tokens_delta_vs_{label}"] = current - baseline
            result[f"input_tokens_reduction_vs_{label}"] = (
                (baseline - current) / baseline if baseline else None
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=1_000)
    parser.add_argument("--body-bytes", type=int, default=512)
    parser.add_argument("--target-index", type=int, default=137)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--model",
        required=True,
        help="LiteLLM model id or proxy alias, for example anthropic/... or openrouter/....",
    )
    parser.add_argument("--max-model-calls", type=int, default=_MAX_MODEL_CALLS)
    parser.add_argument("--strategies", default=",".join(_STRATEGIES))
    parser.add_argument("--tasks", default="all")
    parser.add_argument("--output", type=Path)
    return parser


def _validate(args: argparse.Namespace) -> tuple[tuple[str, ...], set[str] | None]:
    if args.documents <= 0 or not 0 <= args.target_index < args.documents:
        message = "target-index must be inside a positive corpus"
        raise ValueError(message)
    if args.rounds <= 0 or args.max_model_calls <= 0:
        message = "rounds and max-model-calls must be positive"
        raise ValueError(message)

    strategies = tuple(item.strip() for item in args.strategies.split(",") if item.strip())
    unknown = set(strategies) - set(_STRATEGIES)
    if unknown:
        message = f"unknown strategies: {', '.join(sorted(unknown))}"
        raise ValueError(message)

    task_names = {item.strip() for item in args.tasks.split(",") if item.strip()}
    requested_tasks = None if task_names == {"all"} else task_names
    return strategies, requested_tasks


def main() -> None:
    """Run model-selected retrieval through LiteLLM and emit cumulative official usage."""
    args = _parser().parse_args()
    strategies, requested_tasks = _validate(args)
    config = workload.RunConfig(
        documents=args.documents,
        body_bytes=args.body_bytes,
        rounds=1,
        warmups=0,
        target_index=args.target_index,
    )

    with tempfile.TemporaryDirectory(prefix="okf-agentic-") as temporary:
        root = Path(temporary) / "bundle"
        root.mkdir()
        workload._write_bundle(root, config.documents, config.body_bytes)  # noqa: SLF001
        ctx = workload._context(root, config)  # noqa: SLF001
        tasks = trace._tasks(ctx)  # noqa: SLF001
        if requested_tasks is not None:
            tasks = tuple(task for task in tasks if task.name in requested_tasks)
            missing = requested_tasks - {task.name for task in tasks}
            if missing:
                message = f"unknown tasks: {', '.join(sorted(missing))}"
                raise ValueError(message)

        markdown = trace._render_markdown_corpus(root)  # noqa: SLF001
        bundle = load_bundle(root)
        runtimes = {
            "generic-retrieval": _generic_runtime(root),
            "okf-parser": _okf_runtime(bundle),
        }
        samples: list[dict[str, object]] = []
        for task in tasks:
            for strategy in strategies:
                for repetition in range(args.rounds):
                    if strategy == "direct-markdown":
                        result = _run_direct(
                            model=args.model,
                            task=task,
                            markdown=markdown,
                            documents=args.documents,
                        )
                    else:
                        result = _run_tools(
                            model=args.model,
                            task=task,
                            strategy=strategy,
                            runtime=runtimes[strategy],
                            max_calls=args.max_model_calls,
                        )
                    samples.append(
                        {
                            "task": task.name,
                            "category": task.category,
                            "strategy": strategy,
                            "repetition": repetition,
                            **result,
                        }
                    )

    results: list[dict[str, object]] = []
    for task in tasks:
        for strategy in strategies:
            selected = [
                sample
                for sample in samples
                if sample["task"] == task.name and sample["strategy"] == strategy
            ]
            results.append(
                {
                    "task": task.name,
                    "category": task.category,
                    "strategy": strategy,
                    **_aggregate(selected),
                }
            )
    _add_comparisons(results)

    strategy_aggregates = [
        {
            "strategy": strategy,
            **_aggregate([sample for sample in samples if sample["strategy"] == strategy]),
        }
        for strategy in strategies
    ]
    payload = {
        "schema": "okf-agentic-token-cost-v1",
        "measurement": {
            "primary_metric": "cumulative official input/context tokens across all model calls",
            "model": args.model,
            "rounds": args.rounds,
            "tool_selection": "model-selected",
            "transport": "LiteLLM chat completions",
            "token_source": "LiteLLM-normalized provider usage",
            "sdk_retries": 0,
            "tool_results_counted": True,
            "tool_definitions_counted": True,
            "quality": "exact JSON oracle",
        },
        "corpus": {
            "documents": args.documents,
            "body_bytes": args.body_bytes,
            "target_index": args.target_index,
            "same_authored_source_for_all_strategies": True,
        },
        "results": results,
        "strategy_aggregates": strategy_aggregates,
        "samples": samples,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
