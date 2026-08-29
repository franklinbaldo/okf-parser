---
type: Release Note
title: Agent token-cost benchmark
---

## Agent token-cost benchmark

Adds a benchmark whose primary metric is the number of input/context tokens an agent actually consumes to solve equivalent knowledge tasks. It compares full Markdown context, competent generic retrieval, and `okf-parser` structured retrieval against the same deterministic oracle, while keeping bytes and full-representation size as diagnostics only.

The live layer routes models through LiteLLM rather than a provider-specific SDK. Model-selected tool use accumulates provider-reported token usage across every call, including tool definitions, tool results, conversation history, and follow-ups; providers that do not return official usage fail the live measurement instead of falling back to an estimate.
