---
type: RFC
title: Rust core, Python shell
status: proposed
description: Make the native okf-parser binary the product and the single implementation of OKF, with DuckDB, the CLI, MCP and the formatter in Rust, and reduce the Python package to a thin shell that calls the binary over a JSON protocol, the way ruff and uv ship.
---

# RFC 0024: Rust core, Python shell

## Summary

`okf-parser` becomes a Rust program with a Python shell, the way `ruff` and
`uv` ship. The native `okf-parser` binary is the product: it holds every OKF
rule, owns DuckDB, parses the command line, serves MCP and formats Markdown.
The Python package keeps what only Python can hold: the public types, the
`to_okf` serialization of Python objects, and thin functions that call the
binary and validate its JSON answers.

The migration ships in phases, each one a release that removes dependencies.
Phase 1 (0.46.0) moved MCP onto `rmcp` and gave the graph a native API. This
revision replaces the earlier plan of porting ibis to DuckDB *in Python*: code
written in Python now would be deleted by a later phase.

## Motivation

### The dependency tree is the product's installation cost

Before 0.46.0, `pip install okf-parser` resolved 92 runtime packages. FastMCP
alone contributed more than 50 of them — starlette, uvicorn, authlib,
cryptography, httpx, keyring, OpenTelemetry — to serve a protocol that the
binary can speak itself. NetworkX was a hard dependency for three counts.

After phase 1 the runtime tree is 34 packages. What remains is dominated by the
ibis stack: pyarrow (152 MB installed), pandas (50 MB), numpy (33 MB), ibis
(22 MB) and sqlglot (6 MB), about 263 MB to build `memtable`s from rows the Rust
engine already produced. Then come cyclopts, the mdformat family, PyYAML,
ruamel.yaml and markdown-it-py, each of which duplicates something the engine
does or can do.

### Three runtimes implement one format

The Python package, the TypeScript sibling (RFC 0002) and `okf-engine` each
carry OKF semantics, held together by the conformance corpus. Every rule is
written up to three times. The binary is the one implementation every other
surface can call: Python and Node both already spawn it.

## Target architecture

```text
                        okf-parser (Rust binary) — the product
   ┌───────────────────────────────────────────────────────────────────────┐
   │ clap CLI · rmcp MCP server · JSON protocol · formatter · DuckDB (SQL) │
   │                              okf-engine                               │
   │    discovery · parsing · links · digests · diagnostics · ConceptGraph │
   └───────────────▲────────────────────────────────────▲──────────────────┘
                   │ JSON over stdin/stdout             │ same binary
       Python shell (okf_parser)                TypeScript package, npm native
   types · to_okf · load_bundle · sql()
```

- **The binary is the product.** One executable per platform serves Python,
  npm, the TypeScript package and MCP clients. RFC 0003's contract — one wheel,
  one `okf-parser` executable — holds unchanged, and so does
  `native_from_wheel.py`, which reuses the wheel's executable for npm.
- **`okf-engine`** stays the only implementation of OKF semantics and stays
  DuckDB-independent (RFC 0010, decision 2).
- **DuckDB belongs to the binary** (the `duckdb` crate, bundled), as it already
  does in `duckdb-extension/`. Every SQL feature — `sql()`, `apply`, DuckDB
  export, full-text search, relational schemas — runs there.
- **The Python shell** has no OKF logic.

## Decisions

### 1. The binary is the product; the shell speaks JSON to it

The shell calls `okf-parser <command> --json` (or a hidden `__protocol`
command for calls with no CLI equivalent) and validates the answer into
Pydantic models. This is the pattern `rust_core.py` already uses for
`__engine-load`, generalized and versioned.

An in-process PyO3 extension was considered and rejected (see Alternatives):
with every rule in Rust there is no chatty boundary for it to speed up, and it
would split the one executable into a Python extension plus a separate native
build.

### 2. DuckDB belongs to Rust

`okf-parser` links the `duckdb` crate with the `bundled` feature. The Python
shell loses its `duckdb` dependency along with ibis, pandas, numpy and pyarrow.
The cost is a slower CI build (DuckDB compiles from C++) and a larger binary;
both are paid once per release instead of by every install.

### 3. A Rust formatter defines a new canonical format

Formatting moves to a Rust renderer, and **its output becomes the canonical
format**. This is a breaking change: bundles formatted by mdformat will be
reformatted once. Reproducing mdformat byte for byte is not a goal; a stable,
documented, idempotent format is. The formatter's tests pin idempotence and
the rules OKF depends on (frontmatter preserved byte-for-byte, protected
blocks untouched), not mdformat's incidental choices.

### 4. YAML goes through maintained libraries, not hand-written code

Reading uses `yaml-rust2`'s event parser. Writing uses libraries too:
`yaml-edit` (lossless, comment- and style-preserving) for edits to existing
frontmatter, which is what ruamel.yaml does today, and a serializer
(`serde_yaml_ng` or `saphyr`) for frontmatter of new documents. No emitter or
format-preserving editor is written by hand.

### 5. The CLI is clap

Every command moves into the binary's clap CLI as its handler is ported.
Until then the binary keeps delegating unported commands to
`python -m okf_parser.cli`, as it does today. cyclopts leaves when the last
command does.

## What the shell is

What stays in Python, because it is about Python:

- **The public types**: `ConceptRecord`, `LinkRecord`, `Violation`,
  `ValidationReport`, `GraphSummary` and the rest, as Pydantic models that
  validate the binary's JSON.
- **`to_okf` / `dumps` and `SupportsOKF`**: they introspect Python objects, so
  they cannot live anywhere else. They produce data; writing and formatting it
  is the binary's job.
- **Thin API functions**: `load_bundle`, `validate_path`, `Bundle.sql()`,
  `Bundle.graph()` and friends, each a call to the binary.

Everything else — ingestion, validation, SQL, writes, imports, schema export
(including generated Pydantic and Zod source, which is text), formatting, the
CLI and MCP — moves to Rust.

## Public API changes

| Surface | Before | After | Phase |
|---|---|---|---|
| `okf-parser serve` | Python, FastMCP | Rust, `rmcp` | 1 ✓ |
| `serve --transport sse` | supported | removed | 1 ✓ |
| `Bundle.to_networkx()` | returns `nx.MultiDiGraph` | deprecated alias of `bundle.graph().to_networkx()` | 1 ✓ |
| `networkx` | required | optional extra | 1 ✓ |
| `Bundle.concepts` / `.links` / `.reserved` | `ibis.Table` | tuples of `ConceptRecord` / `LinkRecord` / `ReservedRecord` | 2 |
| `load_bundle(engine="native")` | pure-Python ingestion | removed; the binary is required | 2 |
| relational queries on a bundle | ibis expressions | `bundle.sql("SELECT ... FROM concepts")` → rows, run by the binary | 4 |
| `TypedRelations` | ibis tables | `bundle.sql()` over the typed tables | 4 |
| Markdown canonical format | mdformat's | the Rust formatter's | 5 |

There are no compatibility shims: `.execute()` and the ibis API disappear in
the release that removes them, and the changelog says so.

## Graph support

A graph API is a requirement; NetworkX is not.

- **`Bundle.graph()` → `BundleGraph`** (phase 1): `nodes`, `edges`, `summary()`,
  and `to_networkx()` on demand. `ConceptGraph` in `okf-engine/src/graph.rs`
  (petgraph) is the definition; the MCP `graph` tool answers from it natively.
  In phase 2 the shell's `BundleGraph` is built from the binary's answer and the
  pure-Python algorithms in `graph.py` are deleted.
- **Graph as SQL** (phase 4): `concepts` and `links` in the binary's DuckDB
  form a queryable graph; `WITH RECURSIVE` answers reachability and paths.
- **Interop**: `to_networkx()` stays as an optional bridge.

Which graph queries earn native methods beyond `summary()` — neighbors,
backlinks, orphans, cycles, paths — is an open question.

## Migration phases

Each phase is one release. Each one deletes the Python it replaces and the
dependencies only that Python needed.

### Phase 1 — MCP on `rmcp`, native graph (0.46.0, shipped)

`okf-parser serve` runs in the binary with the RFC 0008 profile over stdio and
Streamable HTTP. `graph` is native; other tools are delegated to
`python -m okf_parser.mcp_bridge`. FastMCP and NetworkX left the runtime
dependencies (92 → 34 packages).

### Phase 2 — the shell protocol; `Bundle` as records (0.47.0)

- A versioned JSON protocol between shell and binary, generalizing
  `__engine-load`.
- `load_bundle` always calls the binary. The pure-Python ingestion path
  (`engine="native"`) is deleted; the conformance corpus that proved the two
  equivalent now guards the binary alone.
- `Bundle.concepts` / `.links` / `.reserved` become tuples of records.
  `Bundle.graph()` comes from the binary.
- Consumers inside the package read records instead of ibis tables. ibis stays
  a dependency only for the modules phase 4 replaces.

### Phase 3 — the write engine, then handlers without SQL

Writes move first, because every writer shares one protocol: snapshot the
bundle, stage and validate a candidate, recheck freshness, replace files
atomically. That engine lives in `okf-engine/src/write.rs` and needs no
DuckDB.

- **3a (0.47.0):** the write engine and the single-concept body `edit`, which
  now runs entirely in the binary (`__edit`).
- **3b:** `check`, `inventory` and `classify` as native commands and MCP tools.
- **3c:** the non-SQL `apply` paths (type and field renames) and `dumps`, on
  the write engine. ruamel.yaml leaves.

`import`, `init --infer-schema` and SQL `apply` read or infer through DuckDB,
so they move in phase 4, not here. PyYAML leaves with the last Python reader.

### Phase 4 — DuckDB in Rust

`Bundle.sql()`, SQL `apply`, `import`, schema inference (`init --infer-schema`,
`schema --infer-types`), DuckDB export, full-text search and relational schema
validation run on the `duckdb` crate. ibis, pandas, numpy, pyarrow and the
Python `duckdb` package leave. RFC 0010's extension shares the same Rust code.

### Phase 5 — the Rust formatter

The formatter moves into the binary and defines the new canonical format.
mdformat, its plugins and markdown-it-py leave.

### Phase 6 — the rest

The GraphQL adapter is ported or becomes a separate optional package; git
commit messages as OKF and type packs move with their commands; cyclopts and
`mcp_bridge.py` are deleted. The dependency target at the end is Pydantic.

## Relationship to other RFCs

- **RFC 0003** (one wheel, one executable): unchanged; this RFC depends on it.
- **RFC 0008** (effect-aware MCP writes): unchanged contract.
- **RFC 0010** (native DuckDB extension): shares phase 4's Rust SQL layer.
- **RFC 0015** (canonical Rust Markdown AST): the base of phase 5's formatter.
- **RFC 0021** (bundle relations SQL): the SQL surface `Bundle.sql()` exposes.

## Testing and conformance

The conformance corpus (`conformance/`, pinned to the upstream specification)
is the contract across phases. Python tests become tests of the shell's
contract with the binary; behavior tests move to `cargo test` with the logic
they cover. Every phase keeps the Python, TypeScript and native suites green.

## Risks

- **CI build time.** Bundling DuckDB adds minutes to every native build.
  Mitigation: cache the DuckDB build, and keep the engine crate DuckDB-free so
  its tests stay fast.
- **Per-call process cost.** Each shell call spawns the binary. For bundle
  inspection this is acceptable; if a workload proves otherwise, measure first.
- **Formatter churn.** Phase 5 reformats existing bundles once. It ships alone,
  with a release note that names the change.

## Alternatives considered

- **PyO3 in-process extension.** Faster per call and zero-copy, but it splits
  the one executable into a Python extension plus a separate native build for
  npm (amending RFC 0003), and it only pays off for a chatty boundary, which a
  thin shell does not have. Rejected.
- **Port ibis to DuckDB in Python first.** The earlier revision of this RFC.
  It would rewrite `apply.py` and friends in Python only to delete them in a
  later phase. Rejected.
- **Keep mdformat's output as the canonical format.** Requires a byte-for-byte
  clone of mdformat's renderer. Rejected in favor of a documented Rust format.
- **FastMCP as an optional extra**, **rustworkx**, **Polars**: rejected in the
  first revision; the reasons stand.

## Open questions

1. Which graph queries earn a native method on `BundleGraph`?
2. Does the TypeScript package also become a shell over the binary, or stay an
   independent implementation?
3. Is the GraphQL adapter worth porting, or does it become its own package?
