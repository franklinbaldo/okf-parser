---
type: Release Note
title: MCP served natively on rmcp; graph API without NetworkX
---

# MCP served natively on rmcp; graph API without NetworkX

First phase of [RFC 0024](../../rfcs/0024-rust-native-core.md). The runtime
dependency tree drops from 92 to 34 packages.

## MCP

`okf-parser serve` is now part of the native binary, built on `rmcp`. Tool
names, input schemas and effect annotations are unchanged, and `--allow-write`
still decides whether commit tools are registered. `graph` is answered
natively; every other tool runs the CLI's own service function through
`python -m okf_parser.mcp_bridge`.

Breaking:

- `fastmcp` is no longer a dependency, and `fastmcp.json` is gone.
- `--transport sse` is removed; use `--transport http` (Streamable HTTP at
  `/mcp`).
- `python -m okf_parser.cli serve` is removed; run the `okf-parser` binary.

## Graph

`Bundle.graph()` returns a `BundleGraph` with `nodes`, `edges` and
`summary()` (nodes, edges, weak and strong components, `directed_acyclic`).
NetworkX is now optional: `bundle.graph().to_networkx()` needs
`okf-parser[networkx]`. `Bundle.to_networkx()` still works but emits a
`DeprecationWarning`.
