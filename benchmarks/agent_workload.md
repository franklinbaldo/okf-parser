---
type: BenchmarkProtocol
title: Agent workload and token-cost benchmark
description: Equivalent knowledge tasks measured by agent-consumed context, correctness, and tool diagnostics
---

# Agent workload and token-cost benchmark

This benchmark has two deliberately separate layers.

1. `agent_workload.py` measures tool capability, latency, process startup, and serialized output size across `okf-parser`, a generic Bash/Python baseline, and external OKF tools.
2. `agent_token_cost.py` answers the primary agent question: **how many input/context tokens must be presented to an agent to solve the same task with the same evidence and success criterion?**

The second layer is the decision metric. Bytes, characters, file size, and full-representation tokenization are diagnostics only.

## Primary metric

For one task and one access strategy:

```text
agent_token_cost(task, strategy)
    = sum(input/context tokens actually presented to the model)
```

If an experiment contains multiple model calls, all of their input usage belongs in that sum. Tool results, retrieval payloads, representation instructions, and other context presented to the model count. A future planner that spends model calls deciding what to retrieve must add those calls too; it must not hide planner cost outside the benchmark.

When a live model driver is used, provider/runtime usage is authoritative. The driver must return official `input_tokens` and `output_tokens`. When no model driver is supplied, the benchmark runs a deterministic context trace with the model tokenizer and labels the result as local-tokenizer evidence rather than pretending it is provider usage.

## Three metrics that must not be conflated

The JSON report keeps these separate:

1. `storage_size` — bytes occupied by the authored source.
2. `full_representation_tokens` — tokens required to send a complete representation.
3. agent-consumed `input_tokens` — tokens actually delivered for the task.

A representation can be larger in storage while still winning for agents if its access mechanism lets the agent consume much less of it.

## Equivalent access strategies

The token benchmark uses the same generated authored corpus and the same oracle for every strategy.

### `direct-markdown`

The entire authored Markdown corpus is placed in the knowledge context for each task. This is the simple denominator: no retrieval advantage, no OKF-specific mechanism.

### `generic-retrieval`

Ordinary filesystem/Markdown retrieval scans the same source and emits only the evidence needed for the task. It has no OKF-specific parser semantics.

This lane is essential. If `okf-parser` only beats full-context dumping but does not beat equally selective generic retrieval, the correct conclusion is that **retrieval** creates the token saving, not necessarily OKF.

### `okf-parser`

The same authored corpus is loaded through `okf-parser`; evidence is projected from parser-owned canonical concepts and resolved links. The agent receives only the relevant projection plus the minimum instructions needed to interpret it.

The comparison therefore asks two different questions:

- does selective access beat dumping the whole corpus?
- does OKF-specific structured access beat a competent generic retrieval baseline?

Neither answer is assumed in advance.

## Representative tasks

The deterministic corpus supports six categories:

| task | category |
| --- | --- |
| `lookup_factual` | simple factual lookup |
| `find_concept_type` | concept/type search |
| `cross_reference` | combine evidence from multiple points |
| `relation_navigation` | navigate inbound relations |
| `synthesis` | synthesize several retrieved facts |
| `irrelevant_corpus` | retrieve one small fact when almost all corpus content is irrelevant |

Every task has a deterministic JSON oracle. Access strategies must expose enough evidence to derive that same oracle.

## Quality and `tokens_per_success`

Token economy never wins by lowering answer quality.

In deterministic trace mode, `success` means the retrieved evidence exactly satisfies the oracle. This mode isolates the access/context surface with essentially no model variance; it does **not** claim an LLM produced the answer.

In live mode, the external model driver must return an answer JSON object. The benchmark checks it against the exact same oracle and records official usage.

Across repeated runs:

```text
tokens_per_success = sum(input_tokens across all attempts) / successful_answers
```

This penalizes a strategy that is cheap only because it fails more often.

## Live model driver protocol

`--agent-command` may point at any executable wrapper around a real model/provider. The benchmark sends one JSON object on stdin:

```json
{"model":"MODEL_ID","prompt":"THE EXACT PROMPT PRESENTED TO THE MODEL"}
```

The driver must emit one JSON object on stdout:

```json
{
  "answer": {"...":"task answer"},
  "usage": {
    "input_tokens": 123,
    "output_tokens": 17,
    "total_tokens": 140,
    "calls": 1
  }
}
```

`total_tokens` may be omitted and will be computed as input + output. `calls` defaults to one. If a driver performs several model calls, it must return **cumulative** input/output/total usage and the real call count. `input_tokens` and `output_tokens` are mandatory in live mode; estimates such as characters divided by four are rejected.

The driver remains outside the benchmark core, so provider libraries never become runtime dependencies of `okf-parser`.

A PEP 723 reference driver is included as `benchmarks/litellm_agent_driver.py`. It routes the requested model through LiteLLM and uses the provider-reported `prompt_tokens`, `completion_tokens`, and `total_tokens` normalized by LiteLLM. The benchmark therefore can target OpenAI, Anthropic, Gemini, OpenRouter, Ollama, a LiteLLM proxy alias, or another LiteLLM-supported route without changing the benchmark contract.

For the stronger model-selected retrieval experiment, use `benchmarks/litellm_agentic_workload.py`. It exposes the generic and OKF tool surfaces to the model itself and accumulates official usage across every planning/follow-up call.

## Tokenizer fallback

The deterministic trace uses `tiktoken.encoding_for_model(model)` when that model is known to the installed tokenizer. If it is not known, the explicitly configured fallback encoding is used and the report records `tokenizer_fallback_used: true`.

That fallback result is useful for reproducible comparative traces, but it must not be presented as official runtime usage.

## Context-window failure

The trace records the configured context window. If a strategy requires more input tokens than the window, its status is `context_overflow` and the required token count remains visible. It is not silently truncated into an unfair “saving”.

This matters especially for large direct-Markdown baselines: failure to fit is itself an agent-access result.

## Secondary tool workload

`agent_workload.py` remains useful for questions that are not the primary metric:

- p50/p95 tool latency;
- process startup and serialization;
- stdout/stderr bytes;
- capability support/unsupported;
- external-tool versions and installation provenance.

Those values explain *why* a strategy may be operationally attractive, but serialized bytes are no longer treated as the main proxy for model context cost.

## Running the deterministic token trace

The token benchmark is a PEP 723 script and declares both the local editable `okf-parser` source and the reference tokenizer:

```bash
uv run --script benchmarks/agent_token_cost.py \
  --documents 1000 \
  --rounds 1 \
  --output benchmarks/results/agent-token-cost.json \
  --report benchmarks/results/agent-token-cost.md
```

A larger matrix can change only corpus size while holding task definitions, target selection, tokenizer, and access strategies fixed.

## Running with a real agent

The simple one-call driver keeps deterministic retrieval and changes only the model execution layer:

```bash
uv run --script benchmarks/agent_token_cost.py \
  --documents 1000 \
  --rounds 5 \
  --model anthropic/claude-sonnet-4-5-20250929 \
  --agent-command "uv run --script benchmarks/litellm_agent_driver.py" \
  --output results/live.json \
  --report results/live.md
```

The model id is passed directly to LiteLLM; provider credentials or LiteLLM proxy credentials come from the environment. The driver returns the provider's normalized usage counters.

For model-selected tool navigation, run the dedicated agentic benchmark:

```bash
uv run --script benchmarks/litellm_agentic_workload.py \
  --documents 1000 \
  --rounds 5 \
  --model openrouter/openai/gpt-5 \
  --output results/agentic-live.json
```

## Existing competitor workload

The original tool-level comparison is still reproducible:

```bash
uv run python benchmarks/agent_workload.py \
  --documents 1000 \
  --rounds 5 \
  --warmups 1 \
  --adapters bash-python,okf-parser
```

To include external tools, use `benchmarks/install_okf_competitors.sh` or configure the existing `OKFCLI_CMD`, `SKOSOVSKY_OKF_CMD`, and `OKF_GENERATOR_CMD` environment variables.

## Interpretation rule

No universal OKF victory is expected or desired.

A useful result may be:

- direct context loses badly as corpus size grows;
- generic retrieval captures most of the token saving;
- OKF gains additional efficiency only for relation-heavy tasks;
- OKF adds overhead for simple lookup at small scale;
- one strategy has fewer tokens but lower live success, making `tokens_per_success` worse.

The report should say exactly that when the evidence says it.
