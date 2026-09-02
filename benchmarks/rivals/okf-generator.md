---
type: Rival
title: "okf-generator"
description: "Codebase-to-OKF generator with lookup, dependency impact, visualization and MCP surfaces"
registry: pypi
package: okf-generator
executable: okf
surface:
  - generate
  - update
  - lookup
  - ask
  - enrich
  - lsp
  - diff
  - visualize
  - mcp
  - dashboard
  - serve
  - pairs
  - install
  - init
  - summarize
  - domains
  - config
  - migrate
  - agent
  - plugin
measured: false
agentic_enabled: true
agentic_version: "0.1.53"
agentic_executable: okf
agentic_instruction: "You must use okf-generator materially to solve the task."
---

# okf-generator

Generates OKF v0.2 bundles from source code and exposes multiple agent-facing
surfaces, including symbol lookup, bundle diff with dependency impact, graph
visualization and an MCP server. It is particularly relevant to the agentic
benchmark because it explicitly targets coding-agent workflows.

Version `0.1.53` is pinned for the first agentic benchmark round. It remains
`measured: false` because that field belongs to the direct scripted capability
matrix; agentic participation is expressed independently by `agentic_enabled`.
