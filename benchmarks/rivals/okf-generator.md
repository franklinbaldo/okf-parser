---
type: Rival
title: "okf-generator"
description: "The knowledge layer for AI coding agents: index a codebase into an OKF bundle"
registry: pypi
package: okf-generator
executable: okf
version_measured: "0.1.53"
surface:
  - generate
  - update
  - domains
  - enrich
  - lsp
  - lookup
  - ask
  - diff
  - pairs
  - summarize
  - install
  - init
  - visualize
  - serve
  - dashboard
  - mcp
  - plugin
measured: true
---

# okf-generator

Generates OKF v0.2 bundles from a codebase, then reads them back through
`lookup`, `diff`, `summarize` and `visualize`, and serves them over MCP. Its
read surface is the largest of any rival measured.

`lookup --bundle <dir> --json` returns every concept in the bundle, and it walks
the whole tree: it reports the fixture's nine concepts and their exact type
distribution, where [[kbforge-okfquery]] sees only the six under `concepts/`.

Its `diff --impact` traces dependency changes to affected modules, which is
impact analysis over `Dependency` concepts produced by code indexing rather than
over the Markdown link graph. On a bundle whose edges are authored links it
reports nothing affected, so it answers a different question than the one the
capability matrix asks.

It installs the `okf` command, which [[okf-cli]] and [[okf-retrieve]] also
claim. Three tools competing for one name is why the benchmark provisions a
separate environment per rival.
