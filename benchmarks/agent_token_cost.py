# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "okf-parser",
#     "tiktoken>=0.14,<0.15",
# ]
#
# [tool.uv.sources]
# okf-parser = { path = "..", editable = true }
# ///
"""Measure agent-consumed context tokens for equivalent knowledge-access strategies."""

from __future__ import annotations

import argparse
import json
import math
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import tiktoken

from okf_parser.concepts import concept
from okf_parser.engine import load_bundle

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from okf_parser.bundle import Bundle

_BENCHMARK_DIR = Path(__file__).resolve().parent
if str(_BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARK_DIR))

import agent_workload as workload  # noqa: E402
import generic_agent_baseline as generic  # noqa: E402

_SYSTEM = (
    "Answer the knowledge task using only the supplied context. "
    "Return exactly one compact JSON object, with no prose outside JSON."
)
_STRATEGIES = ("direct-markdown", "generic-retrieval", "okf-parser")
_DEFAULT_MODEL = "gpt-5.6-sol"
_DEFAULT_ENCODING = "o200k_base"
_DEFAULT_CONTEXT_WINDOW = 1_050_000
_AGENT_TIMEOUT_SECONDS = 300


@dataclass(frozen=True, slots=True)
class KnowledgeTask:
    """One answerable task with a deterministic oracle."""

    name: str
    category: str
    prompt: str
    expected: dict[str, object]


@dataclass(frozen=True, slots=True)
class Evidence:
    """Knowledge presented to the model after one access strategy runs."""

    text: str
    retrieved_items: int
    sufficient: bool
    latency_ns: int


@dataclass(frozen=True, slots=True)
class AgentUsage:
    """Cumulative official usage reported by a live model driver."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    calls: int


@dataclass(frozen=True, slots=True)
class AgentResult:
    """One model answer plus provider-reported usage."""

    answer: dict[str, object]
    usage: AgentUsage


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _concept_index(documents: int, concept_id: str) -> int:
    raw = int(concept_id.rsplit("-", 1)[1])
    if not 0 <= raw < documents:
        message = f"concept id outside benchmark corpus: {concept_id}"
        raise ValueError(message)
    return raw


def _neighbor_ids(ctx: workload.TaskContext) -> tuple[str, str]:
    index = _concept_index(ctx.documents, ctx.target_id)
    return (
        workload._concept_id((index + 1) % ctx.documents),  # noqa: SLF001
        workload._concept_id((index + 17) % ctx.documents),  # noqa: SLF001
    )


def _incoming_ids(ctx: workload.TaskContext) -> tuple[str, ...]:
    index = _concept_index(ctx.documents, ctx.target_id)
    candidates = {
        workload._concept_id((index - 1) % ctx.documents),  # noqa: SLF001
        workload._concept_id((index - 17) % ctx.documents),  # noqa: SLF001
    }
    return tuple(sorted(candidates))


def _record_for_index(index: int) -> dict[str, object]:
    return {
        "concept_id": workload._concept_id(index),  # noqa: SLF001
        "title": workload._title(index),  # noqa: SLF001
        "type": workload._TYPES[index % len(workload._TYPES)],  # noqa: SLF001
    }


def _tasks(ctx: workload.TaskContext) -> tuple[KnowledgeTask, ...]:
    target_index = _concept_index(ctx.documents, ctx.target_id)
    next_id, second_id = _neighbor_ids(ctx)
    next_index = _concept_index(ctx.documents, next_id)
    second_index = _concept_index(ctx.documents, second_id)
    records = [
        _record_for_index(target_index),
        _record_for_index(next_index),
        _record_for_index(second_index),
    ]
    counts: dict[str, int] = {}
    for record in records:
        concept_type = str(record["type"])
        counts[concept_type] = counts.get(concept_type, 0) + 1
    target_type_count = sum(
        1
        for index in range(ctx.documents)
        if workload._TYPES[index % len(workload._TYPES)] == ctx.target_type  # noqa: SLF001
    )
    return (
        KnowledgeTask(
            "lookup_factual",
            "lookup factual simples",
            (f"For concept `{ctx.target_id}`, return its concept_id, exact title, and exact type."),
            _record_for_index(target_index),
        ),
        KnowledgeTask(
            "find_concept_type",
            "busca por conceito/tipo",
            f"Return the exact number of concepts whose type is `{ctx.target_type}`.",
            {"type": ctx.target_type, "count": target_type_count},
        ),
        KnowledgeTask(
            "cross_reference",
            "cruza informação de vários pontos",
            (
                f"Starting at `{ctx.target_id}`, follow its two authored Markdown relations. "
                "Return the source and both targets with concept_id, title, and type."
            ),
            {"concepts": records},
        ),
        KnowledgeTask(
            "relation_navigation",
            "navegação por relações",
            f"Return every concept_id with an authored relation to `{ctx.target_id}`.",
            {"target": ctx.target_id, "sources": list(_incoming_ids(ctx))},
        ),
        KnowledgeTask(
            "synthesis",
            "síntese de várias evidências",
            (
                f"For `{ctx.target_id}` plus its two outgoing related concepts, return "
                "counts grouped by exact type."
            ),
            {"source": ctx.target_id, "type_counts": dict(sorted(counts.items()))},
        ),
        KnowledgeTask(
            "irrelevant_corpus",
            "maioria do corpus irrelevante",
            (
                f"Ignore unrelated concepts. Return concept_id, exact title, and exact type "
                f"for `{ctx.target_id}`."
            ),
            _record_for_index(target_index),
        ),
    )


def _render_markdown_corpus(root: Path) -> str:
    parts: list[str] = []
    for path in generic._documents(root):  # noqa: SLF001
        relative = path.relative_to(root).as_posix()
        parts.append(f"<<<FILE {relative}>>>\n{path.read_text(encoding='utf-8')}")
    return "\n".join(parts)


def _source_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in generic._documents(root))  # noqa: SLF001


def _generic_record(root: Path, concept_id: str) -> dict[str, object]:
    payload = generic.show(root, concept_id)
    text = payload["text"]
    if not isinstance(text, str):
        message = "generic show returned non-string text"
        raise TypeError(message)
    frontmatter = generic._frontmatter(root / f"{concept_id}.md")  # noqa: SLF001
    return {
        "concept_id": concept_id,
        "title": frontmatter.get("title"),
        "type": frontmatter.get("type"),
    }


def _generic_evidence(
    task: KnowledgeTask,
    ctx: workload.TaskContext,
) -> tuple[dict[str, object], int]:
    root = ctx.root
    if task.name in {"lookup_factual", "irrelevant_corpus"}:
        return _generic_record(root, ctx.target_id), 1
    if task.name == "find_concept_type":
        payload = generic.filter_type(root, ctx.target_type)
        ids = payload["concept_ids"]
        if not isinstance(ids, list):
            message = "generic type filter returned non-list concept_ids"
            raise TypeError(message)
        return {"type": ctx.target_type, "count": len(ids)}, len(ids)
    if task.name == "relation_navigation":
        payload = generic.backlinks(root, ctx.target_id)
        sources = payload["sources"]
        if not isinstance(sources, list):
            message = "generic backlinks returned non-list sources"
            raise TypeError(message)
        return {"target": ctx.target_id, "sources": sources}, len(sources)

    targets = generic._resolved_targets(  # noqa: SLF001
        root,
        root / f"{ctx.target_id}.md",
    )
    records = [_generic_record(root, ctx.target_id)]
    records.extend(_generic_record(root, target_id) for target_id in targets)
    if task.name == "cross_reference":
        return {"concepts": records}, len(records)
    counts: dict[str, int] = {}
    for item in records:
        concept_type = str(item["type"])
        counts[concept_type] = counts.get(concept_type, 0) + 1
    return {"source": ctx.target_id, "type_counts": dict(sorted(counts.items()))}, len(records)


def _okf_rows(bundle: Bundle) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    concepts = cast(
        "list[dict[str, object]]",
        bundle.concepts.execute().to_dict(orient="records"),
    )
    links = cast(
        "list[dict[str, object]]",
        bundle.links.execute().to_dict(orient="records"),
    )
    return concepts, links


def _okf_evidence(
    task: KnowledgeTask,
    ctx: workload.TaskContext,
    bundle: Bundle,
    concept_rows: list[dict[str, object]],
    link_rows: list[dict[str, object]],
) -> tuple[dict[str, object], int]:
    if task.name in {"lookup_factual", "irrelevant_corpus"}:
        record = concept(bundle, ctx.target_id)
        return (
            {
                "concept_id": record.concept_id,
                "title": record.title,
                "type": record.concept_type,
            },
            1,
        )
    if task.name == "find_concept_type":
        count = sum(row["concept_type"] == ctx.target_type for row in concept_rows)
        return {"type": ctx.target_type, "count": count}, count
    if task.name == "relation_navigation":
        sources = sorted(
            str(row["source_id"]) for row in link_rows if row.get("target_id") == ctx.target_id
        )
        return {"target": ctx.target_id, "sources": sources}, len(sources)

    outgoing = [str(row["target_id"]) for row in link_rows if row.get("source_id") == ctx.target_id]
    wanted = {ctx.target_id, *outgoing}
    by_id = {
        str(row["concept_id"]): row for row in concept_rows if str(row["concept_id"]) in wanted
    }
    ordered_ids = [ctx.target_id, *outgoing]
    records = [
        {
            "concept_id": concept_id,
            "title": by_id[concept_id].get("title"),
            "type": by_id[concept_id].get("concept_type"),
        }
        for concept_id in ordered_ids
    ]
    if task.name == "cross_reference":
        return {"concepts": records}, len(records)
    counts: dict[str, int] = {}
    for item in records:
        concept_type = str(item["type"])
        counts[concept_type] = counts.get(concept_type, 0) + 1
    return {"source": ctx.target_id, "type_counts": dict(sorted(counts.items()))}, len(records)


def _normalized(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _normalized(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        items = [_normalized(item) for item in value]
        return sorted(items, key=_compact)
    return value


def _sufficient(task: KnowledgeTask, payload: dict[str, object]) -> bool:
    return _normalized(payload) == _normalized(task.expected)


def _evidence(  # noqa: PLR0913
    strategy: str,
    task: KnowledgeTask,
    ctx: workload.TaskContext,
    markdown: str,
    bundle: Bundle,
    concept_rows: list[dict[str, object]],
    link_rows: list[dict[str, object]],
) -> Evidence:
    started = time.perf_counter_ns()
    if strategy == "direct-markdown":
        text = (
            "Representation: raw authored Markdown. Each document is delimited by "
            "`<<<FILE path>>>`.\n" + markdown
        )
        payload = task.expected
        retrieved_items = ctx.documents
    elif strategy == "generic-retrieval":
        payload, retrieved_items = _generic_evidence(task, ctx)
        text = (
            "Representation: compact JSON produced by generic filesystem/Markdown retrieval; "
            "concept ids are bundle-relative Markdown paths without `.md`.\n" + _compact(payload)
        )
    elif strategy == "okf-parser":
        payload, retrieved_items = _okf_evidence(task, ctx, bundle, concept_rows, link_rows)
        text = (
            "Representation: compact JSON projected from okf-parser canonical concepts/links; "
            "`concept_id` is the parser-owned identity and relation ids are resolved targets.\n"
            + _compact(payload)
        )
    else:
        message = f"unknown strategy: {strategy}"
        raise ValueError(message)
    return Evidence(
        text=text,
        retrieved_items=retrieved_items,
        sufficient=_sufficient(task, payload),
        latency_ns=time.perf_counter_ns() - started,
    )


def _prompt(task: KnowledgeTask, evidence: Evidence) -> str:
    expected_shape = {key: type(value).__name__ for key, value in task.expected.items()}
    return (
        f"{_SYSTEM}\n\nTASK\n{task.prompt}\n\n"
        f"EXPECTED JSON KEYS/SHAPES\n{_compact(expected_shape)}\n\n"
        f"KNOWLEDGE CONTEXT\n{evidence.text}\n"
    )


def _encoding(model: str, fallback: str) -> tuple[tiktoken.Encoding, bool]:
    try:
        return tiktoken.encoding_for_model(model), False
    except KeyError:
        return tiktoken.get_encoding(fallback), True


def _tokens(encoding: tiktoken.Encoding, text: str) -> int:
    return len(encoding.encode(text, disallowed_special=()))


def _parse_agent_usage(payload: Mapping[str, object]) -> AgentUsage:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        message = "agent driver must return a usage object"
        raise TypeError(message)
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")
    calls = usage.get("calls", 1)
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        message = "agent driver usage must contain integer input_tokens/output_tokens"
        raise TypeError(message)
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    if not isinstance(total_tokens, int):
        message = "agent driver total_tokens must be an integer when supplied"
        raise TypeError(message)
    if not isinstance(calls, int):
        message = "agent driver usage.calls must be an integer"
        raise TypeError(message)
    if calls <= 0:
        message = "agent driver usage.calls must be positive"
        raise ValueError(message)
    return AgentUsage(input_tokens, output_tokens, total_tokens, calls)


def _run_agent(command: Sequence[str], model: str, prompt: str) -> AgentResult:
    completed = subprocess.run(  # noqa: S603 -- user explicitly configures benchmark driver.
        list(command),
        input=_compact({"model": model, "prompt": prompt}) + "\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=_AGENT_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        message = f"agent driver failed ({completed.returncode}): {completed.stderr[-2000:]}"
        raise RuntimeError(message)
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        message = "agent driver must emit one JSON object"
        raise TypeError(message)
    answer = payload.get("answer")
    if not isinstance(answer, dict):
        message = "agent driver answer must be a JSON object"
        raise TypeError(message)
    return AgentResult(answer, _parse_agent_usage(payload))


def _percentile(values: Sequence[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)
    return ordered[index]


def _one_sample(  # noqa: PLR0913
    *,
    task: KnowledgeTask,
    evidence: Evidence,
    encoding: tiktoken.Encoding,
    model: str,
    context_window: int,
    agent_command: Sequence[str] | None,
) -> dict[str, object]:
    prompt = _prompt(task, evidence)
    local_input = _tokens(encoding, prompt)
    reference_output = _tokens(encoding, _compact(task.expected))
    runnable = local_input <= context_window
    if not evidence.sufficient:
        return {
            "status": "insufficient_evidence",
            "success": False,
            "input_tokens": local_input,
            "output_tokens": None,
            "total_tokens": None,
            "reference_output_tokens": reference_output,
            "token_source": "tiktoken_local",
            "calls": 0,
            "retrieved_items": evidence.retrieved_items,
            "retrieval_latency_ns": evidence.latency_ns,
            "runnable_in_context_window": runnable,
        }
    if not runnable:
        return {
            "status": "context_overflow",
            "success": False,
            "input_tokens": local_input,
            "output_tokens": None,
            "total_tokens": None,
            "reference_output_tokens": reference_output,
            "token_source": "tiktoken_local_required",
            "calls": 0,
            "retrieved_items": evidence.retrieved_items,
            "retrieval_latency_ns": evidence.latency_ns,
            "runnable_in_context_window": False,
        }
    if agent_command is None:
        return {
            "status": "trace_ok",
            "success": True,
            "input_tokens": local_input,
            "output_tokens": None,
            "total_tokens": None,
            "reference_output_tokens": reference_output,
            "token_source": "tiktoken_local",
            "calls": 1,
            "retrieved_items": evidence.retrieved_items,
            "retrieval_latency_ns": evidence.latency_ns,
            "runnable_in_context_window": True,
            "quality_kind": "evidence_sufficiency",
        }

    result = _run_agent(agent_command, model, prompt)
    success = _normalized(result.answer) == _normalized(task.expected)
    return {
        "status": "live_ok" if success else "wrong_answer",
        "success": success,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "total_tokens": result.usage.total_tokens,
        "input_tokens_local_diagnostic": local_input,
        "reference_output_tokens": reference_output,
        "token_source": "official_agent_usage",
        "calls": result.usage.calls,
        "retrieved_items": evidence.retrieved_items,
        "retrieval_latency_ns": evidence.latency_ns,
        "runnable_in_context_window": True,
        "quality_kind": "exact_json_oracle",
        "answer": result.answer,
    }


def _aggregate(samples: Sequence[dict[str, object]]) -> dict[str, object]:
    token_values = [int(sample["input_tokens"]) for sample in samples]
    output_values = [
        int(sample["output_tokens"])
        for sample in samples
        if isinstance(sample.get("output_tokens"), int)
    ]
    successful = [sample for sample in samples if sample["success"] is True]
    consumed_for_success = sum(int(sample["input_tokens"]) for sample in samples)
    return {
        "runs": len(samples),
        "successes": len(successful),
        "success_rate": len(successful) / len(samples) if samples else 0.0,
        "input_tokens_mean": statistics.fmean(token_values) if token_values else 0.0,
        "input_tokens_p50": int(statistics.median(token_values)) if token_values else 0,
        "input_tokens_p95": _percentile(token_values, 0.95),
        "output_tokens_mean": statistics.fmean(output_values) if output_values else None,
        "calls_mean": (
            statistics.fmean(int(sample["calls"]) for sample in samples) if samples else 0.0
        ),
        "retrieved_items_p50": int(
            statistics.median(int(sample["retrieved_items"]) for sample in samples)
        )
        if samples
        else 0,
        "tokens_per_success": consumed_for_success / len(successful) if successful else None,
    }


def _with_baseline_savings(results: list[dict[str, object]]) -> None:
    baseline: dict[str, int] = {}
    for result in results:
        if result["strategy"] == "direct-markdown":
            baseline[str(result["task"])] = int(result["input_tokens_p50"])
    for result in results:
        task_name = str(result["task"])
        base = baseline.get(task_name)
        current = int(result["input_tokens_p50"])
        if base is None:
            continue
        result["input_tokens_delta_vs_direct"] = current - base
        result["input_tokens_reduction_vs_direct"] = (base - current) / base if base else None


def _full_okf_projection(
    concept_rows: Iterable[Mapping[str, object]],
    link_rows: Iterable[Mapping[str, object]],
) -> str:
    concepts = [
        {
            "concept_id": row.get("concept_id"),
            "type": row.get("concept_type"),
            "title": row.get("title"),
            "description": row.get("description"),
            "body": row.get("body"),
        }
        for row in concept_rows
    ]
    links = [
        {
            "source_id": row.get("source_id"),
            "target_id": row.get("target_id"),
            "exists": row.get("exists"),
            "origin": row.get("origin"),
        }
        for row in link_rows
    ]
    return _compact({"concepts": concepts, "links": links})


def _fmt_percent(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{100 * float(value):.1f}%"


def _render_report(payload: Mapping[str, object]) -> str:
    measurement = cast("dict[str, object]", payload["measurement"])
    corpus = cast("dict[str, object]", payload["corpus"])
    diagnostics = cast("dict[str, object]", payload["diagnostics"])
    results = cast("list[dict[str, object]]", payload["results"])
    strategy_aggregates = cast(
        "list[dict[str, object]]",
        payload["strategy_aggregates"],
    )
    storage = cast("dict[str, object]", diagnostics["storage_size"])
    full_tokens = cast(
        "dict[str, object]",
        diagnostics["full_representation_tokens"],
    )
    lines = [
        "---",
        "type: Benchmark",
        "title: Agent token cost benchmark",
        "description: Context tokens consumed by equivalent knowledge-access strategies",
        "---",
        "",
        "# Agent token cost benchmark",
        "",
        "Primary metric: **input/context tokens consumed to make a correct answer possible**.",
        "Storage bytes and full-representation tokens are diagnostics, not the winner metric.",
        "",
        "## Run",
        "",
        f"- mode: `{measurement['mode']}`",
        f"- model: `{measurement['model']}`",
        f"- tokenizer: `{measurement['tokenizer']}`",
        f"- corpus: {corpus['documents']} documents x ~{corpus['body_bytes']} body bytes",
        f"- authored Markdown storage: {storage['authored_markdown_bytes']:,} bytes",
        f"- full authored Markdown: {full_tokens['authored_markdown']:,} tokens",
        f"- full OKF canonical projection: {full_tokens['okf_canonical_projection']:,} tokens",
        "",
        "## Per-task results",
        "",
        "| task | strategy | input p50 | p95 | success | items p50 | reduction vs direct |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        (
            "| {task} | {strategy} | {p50:,} | {p95:,} | {success} | {items:,} | {reduction} |"
        ).format(
            task=item["task"],
            strategy=item["strategy"],
            p50=int(item["input_tokens_p50"]),
            p95=int(item["input_tokens_p95"]),
            success=_fmt_percent(item["success_rate"]),
            items=int(item["retrieved_items_p50"]),
            reduction=_fmt_percent(item.get("input_tokens_reduction_vs_direct")),
        )
        for item in results
    )
    lines.extend(
        [
            "",
            "## Strategy aggregates",
            "",
            "| strategy | input p50 | p95 | success | tokens / success |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in strategy_aggregates:
        tokens_per_success = item.get("tokens_per_success")
        rendered_tps = (
            f"{float(tokens_per_success):,.1f}"
            if isinstance(tokens_per_success, (int, float))
            else "—"
        )
        lines.append(
            "| {strategy} | {p50:,} | {p95:,} | {success} | {tps} |".format(
                strategy=item["strategy"],
                p50=int(item["input_tokens_p50"]),
                p95=int(item["input_tokens_p95"]),
                success=_fmt_percent(item["success_rate"]),
                tps=rendered_tps,
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- `direct-markdown` supplies the entire authored corpus to the model.",
            (
                "- `generic-retrieval` uses ordinary filesystem/Markdown retrieval "
                "with no OKF semantics."
            ),
            "- `okf-parser` uses parser-owned canonical concepts and resolved links.",
            (
                "- In deterministic trace mode, success means the retrieved evidence "
                "exactly satisfies the oracle; it does **not** claim a language model "
                "answered correctly."
            ),
            (
                "- In live mode, the external driver must report official "
                "`input_tokens`/`output_tokens`; answer quality is exact against the "
                "same JSON oracle."
            ),
            "- The retrieval planner is deterministic to isolate access/representation cost. "
            "Any future model-driven planning calls must be added to cumulative usage.",
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=1_000)
    parser.add_argument("--body-bytes", type=int, default=512)
    parser.add_argument("--target-index", type=int, default=137)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--fallback-encoding", default=_DEFAULT_ENCODING)
    parser.add_argument("--context-window", type=int, default=_DEFAULT_CONTEXT_WINDOW)
    parser.add_argument(
        "--strategies",
        default=",".join(_STRATEGIES),
        help="Comma-separated: direct-markdown,generic-retrieval,okf-parser.",
    )
    parser.add_argument(
        "--agent-command",
        help=(
            "Optional live model driver. Reads {model,prompt} JSON on stdin and must emit "
            "{answer,usage:{input_tokens,output_tokens,total_tokens?,calls?}} JSON."
        ),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.documents <= 0 or not 0 <= args.target_index < args.documents:
        message = "documents must be positive and target-index must be inside the corpus"
        raise ValueError(message)
    if args.rounds <= 0:
        message = "rounds must be positive"
        raise ValueError(message)
    if args.context_window <= 0:
        message = "context-window must be positive"
        raise ValueError(message)


def main() -> None:
    """Run equivalent tasks through direct, generic-retrieval, and OKF access."""
    args = _parser().parse_args()
    _validate_args(args)
    encoding, fallback_used = _encoding(args.model, args.fallback_encoding)
    requested = tuple(item.strip() for item in args.strategies.split(",") if item.strip())
    invalid = set(requested) - set(_STRATEGIES)
    if invalid:
        message = f"unknown strategies: {', '.join(sorted(invalid))}"
        raise ValueError(message)
    agent_command = shlex.split(args.agent_command) if args.agent_command else None

    config = workload.RunConfig(
        documents=args.documents,
        body_bytes=args.body_bytes,
        rounds=1,
        warmups=0,
        target_index=args.target_index,
    )
    with tempfile.TemporaryDirectory(prefix="okf-agent-token-cost-") as temporary:
        root = Path(temporary) / "bundle"
        root.mkdir()
        workload._write_bundle(root, config.documents, config.body_bytes)  # noqa: SLF001
        ctx = workload._context(root, config)  # noqa: SLF001
        markdown = _render_markdown_corpus(root)
        bundle = load_bundle(root)
        concept_rows, link_rows = _okf_rows(bundle)
        tasks = _tasks(ctx)
        samples: list[dict[str, object]] = []
        for task in tasks:
            for strategy in requested:
                evidence = _evidence(strategy, task, ctx, markdown, bundle, concept_rows, link_rows)
                for repetition in range(args.rounds):
                    sample = _one_sample(
                        task=task,
                        evidence=evidence,
                        encoding=encoding,
                        model=args.model,
                        context_window=args.context_window,
                        agent_command=agent_command,
                    )
                    samples.append(
                        {
                            "task": task.name,
                            "category": task.category,
                            "strategy": strategy,
                            "repetition": repetition,
                            **sample,
                        }
                    )

        aggregates: list[dict[str, object]] = []
        for task in tasks:
            for strategy in requested:
                selected = [
                    sample
                    for sample in samples
                    if sample["task"] == task.name and sample["strategy"] == strategy
                ]
                aggregates.append(
                    {
                        "task": task.name,
                        "category": task.category,
                        "strategy": strategy,
                        **_aggregate(selected),
                    }
                )
        _with_baseline_savings(aggregates)

        okf_projection = _full_okf_projection(concept_rows, link_rows)
        diagnostics = {
            "storage_size": {
                "authored_markdown_bytes": _source_bytes(root),
            },
            "full_representation_tokens": {
                "authored_markdown": _tokens(encoding, markdown),
                "okf_canonical_projection": _tokens(encoding, okf_projection),
            },
            "full_representation_bytes": {
                "authored_markdown": len(markdown.encode()),
                "okf_canonical_projection": len(okf_projection.encode()),
            },
        }

    strategy_aggregates = []
    for strategy in requested:
        selected = [sample for sample in samples if sample["strategy"] == strategy]
        strategy_aggregates.append({"strategy": strategy, **_aggregate(selected)})

    payload = {
        "schema": "okf-agent-token-cost-v1",
        "measurement": {
            "primary_metric": "cumulative agent input/context tokens",
            "mode": "live" if agent_command else "deterministic-context-trace",
            "token_source": "official agent usage" if agent_command else "local tokenizer",
            "model": args.model,
            "tokenizer": encoding.name,
            "tokenizer_fallback_used": fallback_used,
            "context_window": args.context_window,
            "rounds": args.rounds,
            "live_driver_calls_per_task": 1 if agent_command else 0,
            "retrieval_planner": "deterministic and identical task oracle; planner tokens excluded",
        },
        "corpus": {
            "documents": args.documents,
            "body_bytes": args.body_bytes,
            "target_index": args.target_index,
            "same_authored_source_for_all_strategies": True,
        },
        "diagnostics": diagnostics,
        "tasks": [
            {
                "name": task.name,
                "category": task.category,
                "prompt": task.prompt,
                "expected": task.expected,
            }
            for task in tasks
        ],
        "results": aggregates,
        "strategy_aggregates": strategy_aggregates,
        "samples": samples,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(_render_report(payload), encoding="utf-8")
    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
