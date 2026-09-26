---
type: RFC
title: Rust-native core — dropping ibis, networkx and fastmcp
status: proposed
description: Make okf-engine the single implementation of OKF semantics, serve MCP from the native binary on rmcp, give the bundle graph a first-class native API, and replace ibis with DuckDB relations, so the Python package shrinks to a thin binding.
---

# RFC 0024: Rust-native core — dropping ibis, networkx and fastmcp

## Summary

`okf-parser` already ships a Rust binary: every wheel installs `okf-parser`
built from `rust-core/`, and `okf-engine` already implements ingestion
(discovery, frontmatter, Markdown facts, link resolution, digests). But the
binary only re-executes `python -m okf_parser.cli` for everything except two
hidden commands, and the Python package carries the heavy stack behind it:
ibis (with pandas, numpy, pyarrow, sqlglot), networkx for three graph counts,
and FastMCP for the MCP server.

This RFC makes `okf-engine` the one place OKF semantics live, and moves the
surfaces onto it in phases:

1. **MCP on `rmcp` and a native graph** (shipped with this RFC, 0.46.0).
2. **ibis → DuckDB relations** in the Python package.
3. **The DuckDB extension of RFC 0010** replaces row loading.
4. **Port the remaining handlers** tool by tool until Python is a binding.

## Motivation

### The dependency tree is the product's installation cost

Before 0.46.0, `pip install okf-parser` resolved 92 runtime packages. FastMCP
alone contributed more than 50 of them — starlette, uvicorn, authlib,
cryptography, httpx, keyring, OpenTelemetry — to serve a protocol that the
binary can speak itself. NetworkX was a hard dependency for
`number_weakly_connected_components`, `number_strongly_connected_components`
and `is_directed_acyclic_graph`.

After phase 1 the runtime tree is 34 packages. What remains is dominated by the
ibis stack: pyarrow (152 MB installed), pandas (50 MB), numpy (33 MB), ibis
(22 MB) and sqlglot (6 MB), about 263 MB to build `memtable`s from rows the Rust
engine already produced.

### Three runtimes implement one format

The Python package, the TypeScript sibling (RFC 0002) and `okf-engine` each
carry OKF semantics, held together by the conformance corpus. Every new rule is
written up to three times. The engine is the only one that every other surface
can call: Python and Node both already spawn it.

### The relational layer does not need ibis

ibis serves three purposes in this code base:

- building `memtable`s from Python records (`bundle.py`, `duckdb.py`);
- an expression layer over a DuckDB connection (`apply.py`, `typed_relations.py`);
- comparing before/after relations in `apply`.

All three are plain DuckDB SQL. The backend is DuckDB in every code path;
ibis's portability across backends is never exercised.

## Target architecture

```text
            ┌──────────────────────── okf-parser (Rust binary) ─────────────────────────┐
            │  clap CLI     rmcp MCP server     __engine-* commands    DuckDB extension │
            └───────────────────────────────┬───────────────────────────────────────────┘
                                            │
                                   okf-engine (Rust)
             discovery · parsing · links · digests · diagnostics · ConceptGraph
                                            │
                                     DuckDB (relations)
                                            │
                     Python package: thin binding + unported handlers
```

- **`okf-engine`** is the only implementation of OKF semantics. It stays
  DuckDB-independent (RFC 0010, decision 2).
- **DuckDB** is the relational layer for every surface: SQL is the query
  language users already write against `okf.concepts` / `okf.links`
  (RFC 0021).
- **The binary** owns every protocol surface: CLI parsing and MCP.
- **Python** keeps the public `okf_parser` API as a binding and hosts handlers
  until they are ported.

## Public API changes

| Surface | Before | After | Phase |
|---|---|---|---|
| `okf-parser serve` | Python, FastMCP | Rust, `rmcp` | 1 |
| `serve --transport sse` | supported | removed (deprecated by the MCP spec) | 1 |
| `python -m okf_parser.cli serve` | supported | removed; use the `okf-parser` binary | 1 |
| `fastmcp run` / `fastmcp.json` | supported | removed | 1 |
| `Bundle.to_networkx()` | returns `nx.MultiDiGraph` | deprecated alias of `bundle.graph().to_networkx()` | 1 |
| `networkx` | required | optional extra `okf-parser[networkx]` | 1 |
| `Bundle.concepts` / `.links` / `.reserved` | `ibis.Table` | `duckdb.DuckDBPyRelation` | 2 |
| `.execute()` → pandas `DataFrame` | implicit | `.df()` (needs pandas) / `.pl()` (needs polars) / `.fetchall()` | 2 |

Phase 2 is the breaking change most users will notice. It ships in its own
minor release, with `Bundle` tables keeping an `execute()` shim that warns and
returns `.df()` for one release.

## Graph support

A graph API is a requirement; NetworkX is not. The bundle graph gets a
first-class, dependency-free API, in three layers.

### 1. `Bundle.graph()` → `BundleGraph` (phase 1)

`ConceptGraph` in `okf-engine/src/graph.rs` (petgraph) is the definition of the
graph: concepts are nodes, links whose target resolved to a concept are edges,
parallel links stay parallel edges, and a target that never parsed never
becomes a node. `okf_parser.graph.BundleGraph` mirrors it for the Python API:

- `nodes` / `edges`: frozen Pydantic records carrying `path`, `type`, `title`
  and `raw_target`, `origin`;
- `summary()`: nodes, edges, weakly and strongly connected components, and
  `directed_acyclic`;
- `to_networkx()`: the NetworkX bridge, importing it on demand.

The MCP `graph` tool is answered by `ConceptGraph` without starting Python.
`tests/test_mcp.py` asserts that the native tool and the Python service agree.

Once phase 3 lands, `BundleGraph` is built by the engine directly and the
pure-Python algorithms in `graph.py` are deleted.

### 2. Graph queries in SQL (phase 2)

With DuckDB as the relational layer, `okf.concepts` and `okf.links` already
form a queryable graph. `WITH RECURSIVE` answers reachability and paths in
plain DuckDB. The DuckPGQ community extension adds SQL/PGQ
(`MATCH (a)-[e]->(b)`) over the same two tables. This RFC does not depend on
DuckPGQ, but documents a property-graph definition over `okf.concepts` /
`okf.links` so users who load it get a graph with no extra modelling.

### 3. Interop (phase 1)

`BundleGraph.to_networkx()` stays for NetworkX users. A `to_rustworkx()`
bridge can be added on demand; neither is a hard dependency.

Which graph queries deserve native methods beyond `summary()` — neighbors,
backlinks, orphans, cycles, paths between two concepts — is an open question
(below).

## Migration phases

### Phase 1 — MCP on `rmcp`, native graph (0.46.0)

- `rust-core/src/mcp.rs` serves the RFC 0008 profile over stdio and Streamable
  HTTP, with the same tool names, input schemas and effect annotations.
  `--allow-write` still decides whether commit tools are registered.
- `graph` is native. Every other tool is delegated to
  `python -m okf_parser.mcp_bridge`, which validates the arguments against the
  tool's signature and runs the same service function as the CLI. A delegated
  tool's failure is a tool error (`isError: true`), never a crashed server.
- `fastmcp` and `networkx` leave the runtime dependencies.

Delegated calls pay a Python start-up per call. That is acceptable for
interactive agents and is what phases 3 and 4 remove.

### Phase 2 — ibis → DuckDB relations

- `load_bundle` inserts engine rows into an in-process DuckDB connection and
  exposes relations. No Arrow round trip, so no pyarrow.
- `apply.py`, `typed_tables.py`, `typed_relations.py`, `duckdb.py` and
  `search_fts.py` speak SQL to that connection.
- `apply`'s before/after comparison becomes `EXCEPT ALL` in both directions.
- Removes: `ibis-framework`, `pandas`, `numpy`, `pyarrow`, `sqlglot`, `parsy`,
  `toolz`, `atpublic`, `python-dateutil`, `tzdata`.

### Phase 3 — the DuckDB extension feeds the relations

RFC 0010's `okf_concepts` / `okf_links` / `okf_reserved` / `okf_diagnostics`
table functions replace row insertion. Until the extension is published in
DuckDB Community Extensions, loading it from Python needs
`allow_unsigned_extensions`. The phase-2 path stays as the fallback until then,
behind the same public API.

### Phase 4 — port the handlers

Each MCP tool moves from the bridge into the binary once its logic exists in
Rust, in rough order of cost: `inventory`, `check`, `init_*`, `import_*`,
`schema` (JSON, then Zod), `apply`, `duckdb_export`. `mcp_bridge.py` shrinks
with each port and is deleted when empty.

## Relationship to other RFCs

- **RFC 0008** (effect-aware MCP writes): unchanged contract; only the server
  implementation moves.
- **RFC 0010** (native DuckDB extension): becomes phase 3 of this plan.
- **RFC 0015** (canonical Rust Markdown AST): the prerequisite for porting
  formatting and body-aware handlers in phase 4.
- **RFC 0021** (bundle relations SQL): the SQL surface that replaces ibis
  expressions, and the home of graph-as-SQL.

## What stays in Python, and why

- **`mdformat` formatting.** `markdown_style.py` relies on mdformat internals,
  and no Rust CommonMark+GFM formatter reproduces its output. Porting it means
  writing a renderer on top of RFC 0015's AST; until then it stays.
- **Pydantic projection and `okf_parser.serialization`.** These are Python-
  object features: they generate or consume Python classes and belong in the
  binding by nature.
- **The GraphQL adapter.** It is an optional extra that serves Python callers.

## Testing and conformance

The conformance corpus (`conformance/`, pinned to the upstream specification)
is the contract between phases: every phase must keep the Python, TypeScript
and native suites green on it. Each ported tool keeps its Python test until the
bridge entry is deleted, and gains a native test that drives the served tool.
`tests/test_mcp.py` drives the real binary over stdio; `cargo test` covers the
server profile and `ConceptGraph`.

## Alternatives considered

- **Make FastMCP an optional extra.** Removes the dependency from default
  installs, but keeps two MCP stacks (Python and TypeScript) and keeps the
  protocol in the slowest layer. Rejected in favour of `rmcp`.
- **Replace NetworkX with rustworkx.** Still a Python dependency, with an API
  close to but not identical to NetworkX, so `to_networkx()` callers break
  anyway. Rejected; offered as an optional bridge only.
- **Polars instead of DuckDB as the relational layer.** Polars has no SQL
  surface compatible with the `okf.*` tables users already query, and DuckDB
  is already a required dependency. Rejected.
- **Port everything in one release.** Breaks every surface at once and cannot
  be reviewed. Rejected in favour of phases that each ship on their own.

## Open questions

1. Which graph queries earn a native method on `BundleGraph` beyond
   `summary()`?
2. Does phase 2 ship a pandas-returning `execute()` shim, or break cleanly?
3. Should the TypeScript package delegate to the binary as well (its MCP server
   uses `@modelcontextprotocol/sdk`), or stay an independent implementation?
4. What is the performance budget for delegated tool calls before phase 4 ports
   them?
