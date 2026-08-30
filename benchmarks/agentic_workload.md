---
type: Documentation
title: Live agentic token-cost benchmark
description: Model-selected retrieval benchmark routed through LiteLLM with cumulative provider usage
---

# Live agentic token-cost benchmark

`litellm_agentic_workload.py` is the end-to-end layer of the agent token benchmark. Unlike the deterministic context trace, it does not preselect the evidence that the model receives.

The model must decide how to access the same authored knowledge. LiteLLM supplies one normalized Chat Completions surface while routing the same benchmark to any supported provider/model or to a LiteLLM proxy alias.

## Provider neutrality

The benchmark does not import a provider SDK and does not special-case OpenAI, Anthropic, Gemini, OpenRouter, Ollama, or another runtime. The `--model` value is passed directly to LiteLLM.

Examples of valid routing styles include provider-qualified model ids such as `anthropic/...`, `openai/...`, `openrouter/...`, or a model alias exposed by a LiteLLM proxy. Provider credentials remain in the environment expected by LiteLLM.

The access strategy, task oracle, tool definitions, and accounting logic stay identical when the provider changes. Provider comparisons therefore require separate runs with the same corpus/task configuration rather than provider-specific benchmark code.

## Strategies

`direct-markdown` receives the entire authored Markdown corpus in one model request.

`generic-retrieval` receives two ordinary filesystem/Markdown tools:

- `read_document(concept_id)` returns one authored Markdown document;
- `search_text(query, offset, limit)` performs literal corpus search and returns matching concept ids, compact matching lines, and an exact hit count.

`okf-parser` receives three canonical structured tools:

- `get_concept(concept_id)` returns title, type, body, and resolved outgoing targets;
- `filter_concepts(concept_type, offset, limit)` filters canonical concepts and returns the exact count;
- `incoming_links(concept_id, offset, limit)` returns canonical backlink sources.

The generic baseline is intentionally competent. It can search and read the same authored source without OKF-specific semantics. If generic retrieval costs fewer tokens at equal quality, the benchmark records that as a loss for OKF.

## Accounting

For every task execution:

```text
input_tokens = sum(response.usage.prompt_tokens for every model call)
output_tokens = sum(response.usage.completion_tokens for every model call)
total_tokens = sum(response.usage.total_tokens for every model call)
```

LiteLLM normalizes provider responses into the OpenAI-style usage shape. The benchmark accepts those provider-reported counters as the live source of truth. If a routed provider does not return integer prompt/completion usage, the run fails instead of silently substituting a tokenizer estimate.

This means tool planning, tool definitions, tool results returned in `tool` messages, accumulated conversation state, and follow-up calls are all charged when they enter model context.

Every LiteLLM call sets `num_retries=0`. Hidden SDK retries would otherwise create requests whose tokens are not represented in the successful response usage accumulated by the benchmark.

Tool execution latency is measured outside the tool payload, so benchmark telemetry does not itself inflate model context.

## Quality

Every task uses the same deterministic JSON oracle as `agent_token_cost.py`. A run succeeds only when the final JSON answer equals that oracle after order normalization.

`tokens_per_success` is:

```text
sum(input_tokens across all attempts) / successful_answers
```

A strategy cannot improve the efficiency score by failing cheaply.

## Tasks

The same six tasks are used in all strategies:

- factual lookup;
- exact concept/type count;
- cross-reference traversal;
- incoming relation navigation;
- multi-evidence synthesis;
- sparse lookup in a mostly irrelevant corpus.

## Repetitions

One round is useful as a live smoke test. The final comparison should use repeated runs, for example five repetitions, before interpreting median or p95.

A low-cost single-task smoke:

```bash
uv run --script benchmarks/litellm_agentic_workload.py \
  --documents 1000 \
  --rounds 1 \
  --model anthropic/claude-sonnet-4-5-20250929 \
  --tasks relation_navigation \
  --output /tmp/agentic-smoke.json
```

The final 1k run through another route can keep everything except the model identical:

```bash
uv run --script benchmarks/litellm_agentic_workload.py \
  --documents 1000 \
  --rounds 5 \
  --model openrouter/openai/gpt-5 \
  --output benchmarks/results/agentic-live-1k.json
```

The corresponding provider key or LiteLLM proxy credentials must be available to the script. Live calls are deliberately not run in ordinary CI because they consume paid model capacity and introduce external variance.

## Interpretation

The deterministic trace answers: “once the right evidence is selected, how many context tokens does each representation require?”

The live agentic benchmark answers the stronger question: “starting from the task, how many context tokens does the agent actually consume while deciding what to retrieve, retrieving it, and producing a correct answer?”

The second result is the decisive one for claims about agent efficiency. The deterministic result remains useful because it isolates representation overhead from navigation/planning overhead.
