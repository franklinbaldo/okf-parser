---
type: Release Note
title: first native DuckDB extension slice
---

- Add the first RFC 0010 implementation slice: an experimental Rust loadable DuckDB extension exposing `okf_concepts(root)` directly from the shared `okf-engine`, emitting DuckDB data chunks without subprocess/whole-bundle JSON transport and keeping the evolving extension toolchain isolated from the stable workspace release graph.
