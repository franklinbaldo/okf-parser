---
type: Rival
title: "okf-retrieve"
description: "MCP-first agent retrieval over Open Knowledge Format bundles"
registry: pypi
package: okf-retrieve
executable: okf
version_measured: "0.1.1"
surface:
  - validate
  - search
  - graph
  - serve-mcp
homepage: https://github.com/scionoftech/okf-retrieve
measured: true
---

# okf-retrieve

Serves a bundle to an MCP-capable agent so it fetches stubs and walks the link
graph on demand instead of flattening every document into context. It prints a
resolved link graph but does not answer questions about it.

It fails a bundle whose only defect is an unresolved link, which OKF v0.2 says
does not make a bundle non-conformant.

It installs the `okf` command, which [[okf-cli]] also claims.
