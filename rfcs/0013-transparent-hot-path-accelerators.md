---
type: RFC
title: Transparent hot-path accelerators over DuckDB-first relational ergonomics
status: proposed
description: Keep DuckDB/Ibis as the convenient relational interface while allowing benchmark-selected, disposable in-memory indexes to transparently accelerate exact lookup and adjacency without becoming a second semantic backend.
---

# RFC 0013: Transparent hot-path accelerators over DuckDB-first relational ergonomics

## Summary

RFC 0012 establishes one canonical relational read service for `lookup`,
relations, `diff`, `impact`, `context`, CLI, MCP, and agents. The initial
performance comparison with `okf-generator` exposed a useful physical-design
fact:

- DuckDB is excellent at relational scans, joins, filtering, aggregation, and
  ad-hoc SQL;
- indexed SQLite is dramatically faster for singleton key lookup and local
  adjacency;
- a native in-memory Rust index can make those hot operations faster still.

This RFC therefore does **not** choose SQLite instead of DuckDB, and it does not
replace the relational service with a Rust-only API.

The architectural rule is:

> **DuckDB-first ergonomics, accelerator-transparent performance.**
>
> DuckDB/Ibis remains the convenient relational interface and universal
> fallback. A hot-path accelerator may be built lazily from the same canonical
> relation snapshot and used internally for operations where benchmarks show a
> material win. Callers do not select, observe, or depend on the accelerator.

```text
authored OKF / explicit adapters
        ↓
canonical semantic engine
        ↓
canonical relation snapshot
        │
        ├──────────────→ DuckDB / Ibis
        │                 SQL, joins, scans,
        │                 filters, aggregation,
        │                 diff/impact/context,
        │                 ad-hoc exploration
        │
        └──────────────→ optional hot accelerator
                          exact id → ordinal
                          outgoing adjacency
                          incoming adjacency
                          other indexes only after profiling

shared relational read service / planner
        ↓
lookup / relations / query / diff / impact / context
        ↓
CLI / MCP / agents
```

The accelerator is a **physical access index**, not an authoritative store, not
a user-facing backend, and not a second semantic model.

## Motivation

### DuckDB's convenience is part of the product

The value of DuckDB is not merely raw query speed. It gives the project a very
low-friction relational surface:

- ordinary SQL for inspection and debugging;
- Ibis expressions for structured, typed consumers;
- joins and aggregations without inventing bespoke graph/query APIs;
- easy interoperability with Arrow, files, Python, and external analytical
  workflows;
- one place where an agent or maintainer can ask an unanticipated relational
  question.

An optimization that forces callers to understand a separate key/value engine,
SQLite schema, graph store, or Rust data structure would lose that advantage.
This RFC forbids that coupling.

### The benchmark exposed a real hot-path asymmetry

A same-host benchmark against `okf-generator` v0.1.53 used a synthetic 50,000
concept bundle with one relation per concept. Equivalent warm SQL showed:

| Operation at 50k concepts | SQLite | DuckDB |
| --- | ---: | ---: |
| point lookup by concept id | ~7.75 µs | ~739 µs |
| incident-edge lookup | ~9.53 µs | ~1,243 µs |
| type filter + order + limit 80 | ~9,051 µs | ~2,549 µs |
| count by type | ~1,038 µs | ~1,151 µs |

The result is not "SQLite wins". It says the physical structures are optimized
for different workloads:

- B-tree/keyed access is ideal for singleton navigation;
- DuckDB's vectorized execution is strong for set-oriented relational work.

A follow-up Rust microbenchmark over 50,000 concepts measured approximately:

- `HashMap<concept_id, ordinal>` lookup: **20.8 ns**;
- adjacency-vector access by ordinal: **1.6 ns**.

Those Rust numbers are intentionally treated as a *microbenchmark*, not as an
end-to-end product latency claim: they omit API serialization, result hydration,
and language/runtime boundaries. They are evidence that a database is not
necessarily the right physical primitive for the smallest hot operations.

The benchmark material lives outside this RFC's product branch and is only
motivation. Implementation acceptance requires reproducible benchmarks against
the actual shared read service.

## Decision

### 1. DuckDB/Ibis remains the relational interface

The public and internal relational contract established by RFC 0012 remains
unchanged.

A caller may always express a relational operation through the shared service,
and `query --sql` remains a DuckDB-oriented power-user surface. No public CLI,
MCP tool, Python API, TypeScript API, or adapter accepts an `engine=sqlite`,
`engine=hashmap`, or similar hot-path selector.

The accelerator is invisible to callers.

### 2. Accelerators are derived, disposable physical indexes

An accelerator is constructed only from a canonical relation snapshot whose
identity is already known to the shared read service.

It has these properties:

- **one-way derivation**: canonical relations → accelerator;
- **read-only**: no semantic write originates in the accelerator;
- **disposable**: it can be dropped at any time without information loss;
- **rebuildable**: rebuilding from the same snapshot must reproduce the same
  observable results;
- **non-authoritative**: disagreement with the canonical provider is a bug in
  the accelerator;
- **private**: physical ordinals, hash-table layout, SQLite rowids, or other
  implementation handles never enter public payloads.

This is analogous to an index, not to a second database of record.

### 3. The first accelerator target is native in-memory keyed navigation

The preferred first implementation candidate is deliberately smaller than
SQLite:

```text
concept_id ──HashMap──→ ordinal
                        │
                        ├→ concepts[ordinal]
                        ├→ outgoing[ordinal] = [edge ordinal / target ordinal…]
                        └→ incoming[ordinal] = [edge ordinal / source ordinal…]
```

A plausible Rust shape is:

```rust
struct HotIndex {
    concept_by_id: HashMap<ConceptId, u32>,
    outgoing: Vec<Vec<u32>>,
    incoming: Vec<Vec<u32>>,
}
```

The exact representation is **not normative**. CSR-style flat arrays,
`hashbrown`, perfect hashing, sorted vectors, SQLite `:memory:`, `redb`, or
another representation may replace it if measured end-to-end performance,
memory use, portability, or maintenance cost is better.

The RFC standardizes the role and invariants of the accelerator, not a crate.

### 4. SQLite remains a valid reference/fallback accelerator, not the design center

SQLite has useful properties:

- trivial construction from tabular relations;
- mature B-tree indexes;
- portability and debuggability;
- good reference semantics for keyed SQL;
- excellent measured point/adjacency performance.

It may therefore be useful as:

- a Python/portable fallback when native acceleration is unavailable;
- a benchmark/reference implementation;
- an implementation choice on a platform where the native representation is
  not worth its complexity.

But no consumer should know whether the hot index is SQLite, a Rust `HashMap`,
or absent.

### 5. Routing is by operation shape, never by domain semantics

A small planner inside the shared read service may route an operation to the
accelerator only when its relational semantics are already fixed.

Initial candidate routing:

| Operation shape | Preferred physical path |
| --- | --- |
| exact `concept_id` → concept identity/handle | hot accelerator |
| immediate outgoing/incoming neighbors by exact id | hot accelerator |
| exact path/id resolution used as a seed | hot accelerator |
| broad filters, sort, limit, projection | DuckDB/Ibis |
| joins and aggregations | DuckDB/Ibis |
| arbitrary SQL | DuckDB |
| relational `diff` | canonical relational engine / DuckDB |
| set-oriented or recursive `impact` | canonical relational engine / DuckDB/native provider |
| `context` | planner may combine both, preserving one observable contract |

The planner must not route based on whether a concept is a Function, legal
concept, dependency, or any other domain type. Physical routing depends only on
operation shape and measured cost.

### 6. Complex graph work must not degrade into N hot lookups by accident

Fast adjacency makes a Python/Rust loop tempting. That does not make it the
right implementation for `impact` or other large traversals.

The planner must distinguish:

- **local navigation**: one/few exact nodes and immediate neighbors;
- **set traversal**: many frontier nodes, recursive reachability, snapshot-aware
  path support, grouping, filtering, or aggregation.

The latter should remain set-oriented when that is cheaper and clearer. A
1-millisecond relation query repeated 10,000 times is worse than one relational
operation; a 2-nanosecond adjacency read may change the crossover point, but the
crossover is a benchmark question, not an architectural assumption.

### 7. Accelerator identity and invalidation are snapshot-based

A hot index is valid only for one canonical relation snapshot.

Its cache identity must include at least:

```text
(
  parsed_digest / canonical snapshot identity,
  relation_schema_version,
  semantic_engine_identity,
  accelerator_implementation_version,
)
```

If any identity component changes, the accelerator is discarded and rebuilt.
No incremental repair algorithm is required initially.

This intentionally prefers cheap delete/rebuild semantics over cache coherence
complexity.

### 8. Construction should be lazy or lifecycle-aware

Building an accelerator has a cost. The shared service should not eagerly pay
that cost merely because the feature exists.

Permitted strategies include:

- build on the first eligible hot operation;
- build when a long-lived MCP/server session opens a sufficiently large
  snapshot;
- skip the accelerator entirely below a benchmark-derived corpus/query
  threshold;
- prebuild it alongside a compiled image only when profiling demonstrates a
  net win.

The policy must be deterministic for a given configuration and observable only
through diagnostics/telemetry, never through different query semantics.

### 9. Payload hydration remains canonical

The accelerator should store the minimum data necessary to find canonical
records quickly.

For example, an exact-id index may return an ordinal, stable internal handle, or
canonical id. Rich payload construction still comes from the same relation
snapshot/projection contract used without acceleration.

This avoids duplicating descriptions, frontmatter, diagnostics, projection
rules, or other semantically meaningful fields merely to make a hash lookup
fast.

A later benchmark may justify storing a compact immutable hot payload, but that
must remain a projection whose parity is tested.

### 10. Fallback must always preserve behavior

If the accelerator is absent, disabled, unsupported, over memory budget, or
fails to build, the same operation must remain available through the canonical
relational path unless the canonical path itself is unavailable.

Performance may change. Results may not.

## Why not just use SQLite for everything?

Because the benchmark already shows that this would exchange one optimization
problem for another. At 50k concepts, DuckDB was about 3.55× faster for the
measured ordered type-filter query, while type count was near parity. More
importantly, DuckDB preserves the project's broad relational ergonomics.

SQLite is an excellent physical index for some shapes. It is not a reason to
replace the analytical/query surface.

## Why not expose the Rust index directly?

Because the project would immediately grow two query languages:

1. relational DuckDB/Ibis;
2. bespoke graph/key operations over Rust structures.

That would leak physical optimization into product semantics and repeat the
architectural mistake RFC 0012 is designed to avoid.

The Rust index should behave like an invisible CPU cache: useful because it is
fast, boring because callers do not need to know it exists.

## Performance model

Implementation work must measure at least four costs separately:

1. **canonical load** — producing the relation snapshot;
2. **accelerator build** — constructing keyed/adjacency indexes;
3. **hot operation latency** — exact lookup and immediate adjacency;
4. **break-even** — number/mix of calls after which construction pays for
   itself relative to DuckDB-only execution.

Memory use is a first-class metric. A candidate that saves 500 µs but consumes
hundreds of megabytes for a normal bundle may lose even if its isolated latency
wins.

Benchmarks must include at least small, medium, and large corpora and report
absolute latency as well as multipliers.

## Memory policy

The accelerator is optional and must respect an explicit memory budget or
resource policy.

The implementation should prefer compact integer ordinals for adjacency rather
than repeated concept-id strings. The planner may decline to construct the
accelerator when its estimated footprint exceeds the configured/default budget.

No correctness behavior may depend on sufficient memory for the accelerator.

## Observability

The shared service may expose diagnostic metadata such as:

```json
{
  "physical_plan": "hot_index",
  "accelerator": "native-memory-v1",
  "accelerator_build_ms": 3.2,
  "snapshot": "…"
}
```

Such metadata is diagnostic only. Normal product payloads must not require it
and tests of semantic output must not branch on it.

## Compatibility with RFC 0012

This RFC refines RFC 0012's statement that a third feature-specific
scanner/index is forbidden.

The prohibition remains: no feature may independently parse authored files or
construct its own semantic model.

What this RFC permits is narrower:

> a **physical index derived exclusively from the already-canonical relation
> snapshot**, hidden behind the same shared relational read service, with
> provider-parity tests and unconditional canonical fallback.

Therefore the accelerator is not a competing scanner/index in the semantic
sense RFC 0012 prohibits.

## Alternatives considered

### DuckDB only

Simplest implementation and still sufficiently fast for many workloads.
Rejected as a permanent restriction because benchmarked singleton/adjacency
operations leave two to three orders of magnitude of avoidable physical-access
overhead.

DuckDB-only remains the mandatory fallback and may remain the only path until
an accelerator clears the acceptance gates.

### SQLite `:memory:` as the only accelerator

Very attractive and measured to be fast. Rejected as a normative requirement
because an even simpler native in-memory structure may be substantially faster
and cheaper, especially when the Rust semantic engine already has canonical
relations in memory.

SQLite remains a valid candidate/fallback/reference implementation.

### Persistent SQLite sidecar

Rejected initially. Persistence adds invalidation, filesystem lifecycle,
locking, cleanup, and corruption surface while the data is fully derivable.
Revisit only if accelerator build cost becomes material in real workloads.

### Dedicated graph database

Rejected. It adds operational complexity and a competing query surface for a
workload currently served by compact adjacency indexes plus relational set
operations.

### NetworkX as the hot store

Rejected. NetworkX remains useful as a projection for graph algorithms but is
not designed to minimize exact-id/adjacency latency or memory overhead for this
hot path.

## Acceptance invariants

An implementation of this RFC is acceptable only when all of the following are
executable tests or benchmark gates:

1. **Transparent parity** — every accelerated operation returns the same
   canonical observable result as acceleration-disabled execution over the same
   snapshot.
2. **No public engine selector** — CLI, MCP, Python, TypeScript, and adapters do
   not expose SQLite/native/hash-map choice as product semantics.
3. **No semantic reparse** — accelerator construction consumes canonical
   relations only; it never reads authored Markdown/source files directly.
4. **Snapshot invalidation** — changing snapshot identity invalidates the entire
   accelerator; a stale accelerator can never answer a query.
5. **Delete/rebuild equivalence** — destroying and rebuilding the accelerator
   cannot change semantic output.
6. **Canonical fallback** — disabling or failing accelerator construction keeps
   all affected operations functional through the relational provider.
7. **Hot-path evidence** — the chosen initial accelerator demonstrates a
   material end-to-end win for exact lookup and adjacency, not merely an
   isolated data-structure microbenchmark.
8. **Build-cost evidence** — benchmark reports construction time and an explicit
   break-even query count/mix.
9. **Memory evidence** — benchmark reports peak/steady accelerator memory and
   enforces the configured/default budget behavior.
10. **Set-query non-regression** — broad filter/join/aggregation workloads are
    not routed through the hot index when DuckDB/native relational execution is
    faster or semantically simpler.
11. **Impact semantics preserved** — snapshot-aware RFC 0012 `support_depths`
    behavior is identical with acceleration on and off, including rejection of
    synthetic mixed-snapshot paths.
12. **Diagnostic-only physical plan** — changing the planner's physical choice
    may change diagnostics/performance, never the normal semantic payload.

## Implementation order

After RFC 0012's shared relation contract exists:

1. add benchmark fixtures for exact lookup and immediate adjacency;
2. define a private `HotLookup`/accelerator interface behind the shared service;
3. implement the smallest native in-memory candidate from canonical relations;
4. run parity, build-cost, memory, and break-even benchmarks against DuckDB-only;
5. route exact-id and immediate-neighbor operations only if the evidence clears
   the gate;
6. keep SQLite `:memory:` as a portable/reference candidate where useful;
7. expand the accelerated operation set only from profiling evidence;
8. do not persist the accelerator until repeated construction is demonstrated
   to be a real bottleneck.

## Non-goals

This RFC does not:

- replace DuckDB or Ibis;
- define a second semantic backend;
- expose a user-selected physical query engine;
- move arbitrary SQL to SQLite;
- prescribe a Rust hash-map crate or graph representation;
- require persistence or incremental cache repair;
- alter OKF authoring, parsing, identity, digest, diff, impact, or context
  semantics;
- turn the hot index into a domain ontology or code-specific graph.

## Consequence

The intended end state is deliberately asymmetric:

```text
                    convenient / general
                         DuckDB + Ibis
                         ↑          ↑
                         │ same canonical semantics
                         ↓          ↓
agent operation → shared read service / planner
                         ↑
                         │ invisible acceleration
                         ↓
                 native hot index when useful
```

The project keeps the part of DuckDB that makes it unusually easy to use while
refusing to pay analytical-engine overhead for the handful of operations where
a tiny derived in-memory index is measurably better.
