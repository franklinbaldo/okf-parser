---
type: RFC
title: Workload-specific physical materialization
status: proposed
description: Treat DuckDB as a relational transformation and query engine over canonical OKF relations, while materializing disposable physical targets only when a workload benefits from their storage or indexing properties.
---

# RFC 0014: Workload-specific physical materialization

## Summary

RFC 0012 defines the canonical relational contract. RFC 0013 permits transparent hot-path accelerators derived from that contract. This RFC makes the more general physical-design rule explicit:

> **Canonical relations are the semantic authority. DuckDB is a transformation/query engine over those relations. Materialized representations are workload-specific, derived, disposable physical targets.**

A hot accelerator is therefore one kind of physical target, not the definition of the materialization layer.

```text
authored OKF / explicit adapters
        ↓
canonical semantic engine
        ↓
canonical relation snapshot
        ↓
DuckDB transformation / query layer
        │
        ├── query directly when its execution model fits
        │
        ├──→ SQLite hot target
        │      point lookup / local adjacency
        │
        ├──→ Parquet target
        │      persisted columnar scans / analytical exchange
        │
        └──→ Arrow target
               interchange / in-memory columnar execution

shared relational service / planner
        ↓
CLI / MCP / agents / integrations
```

Only targets with an implemented workload and evidence belong in product code. The diagram names plausible target classes; it does not require empty adapters, enums, or public selectors for formats that have not cleared their own acceptance gate.

## Decision

### 1. DuckDB need not be the persisted representation

Using DuckDB as the relational engine does not imply that canonical relations must first be copied into persistent DuckDB tables or stored in a DuckDB database file.

DuckDB may operate over canonical relations directly, including through transient tables, views, Arrow-compatible inputs, native providers, or other semantically equivalent relation sources. Whether those inputs themselves are materialized is a physical-plan decision.

The project should not pay for a DuckDB materialization merely because DuckDB performs a query or transformation.

### 2. Physical targets are selected by workload properties

A physical target exists to provide properties that materially fit a workload better than the universal relational path.

Examples include:

| Workload shape | Candidate physical target |
| --- | --- |
| exact `concept_id` lookup | indexed SQLite or native keyed index |
| immediate incoming/outgoing adjacency | indexed SQLite or native adjacency index |
| broad relational query / joins / aggregation | DuckDB direct execution |
| persisted columnar scans / analytical distribution | Parquet |
| in-memory columnar interchange | Arrow / Arrow IPC |

This table is illustrative, not a permanent routing policy. Benchmarks decide whether a target is worth building and when.

### 3. Every materialized target is derived and non-authoritative

A target must be produced exclusively from canonical relations. It must not parse authored Markdown, resolve links independently, derive a competing `concept_id`, or otherwise recreate semantic logic.

Every target is:

- **derived** — canonical relations → physical representation;
- **read-only with respect to semantics** — no canonical write originates there;
- **disposable** — deletion loses no semantic information;
- **rebuildable** — the same canonical snapshot yields equivalent observable data;
- **versioned by physical contract** — stale layouts are invalidated rather than reinterpreted silently.

RFC 0013 accelerators satisfy these rules and remain valid implementations of this narrower hot-path role.

### 4. DuckDB is the preferred bulk transformation bridge when it is cheap

DuckDB's ability to read, transform, and write multiple relational formats is a product advantage. A target may therefore be built with operations such as cross-database CTAS or `COPY` when that avoids bespoke row-by-row conversion code.

This does **not** make DuckDB output the semantic source of truth. DuckDB is executing a physical transformation over already-canonical relations.

A direct native writer remains allowed when benchmarks show that passing through DuckDB adds material cost or complexity. The observable target must still derive from the same canonical relation contract.

### 5. Do not create speculative target APIs

The materialization boundary may be generic, but concrete product APIs should name only implemented targets.

For example, an implementation may expose an internal function such as `materialize_sqlite_hot(...)` once SQLite exists. It should not introduce `target="parquet"`, `PhysicalTarget::Arrow`, or equivalent placeholders until those targets have real semantics, tests, lifecycle rules, and benchmark evidence.

This keeps the architecture extensible without making unsupported possibilities part of the contract.

### 6. Materialization and routing are separate decisions

Building a target does not require every eligible operation to use it. A planner may consider:

- target build cost;
- expected query count;
- corpus size;
- memory/disk budget;
- warm versus cold lifecycle;
- whether a previously built target can be reused safely.

The planner remains invisible to callers. Physical-plan changes may affect performance and diagnostics, never canonical result semantics.

## Relationship to RFC 0013

RFC 0013 remains the normative rule for transparent hot-path acceleration. This RFC generalizes the physical layer around it:

```text
physical materialization
    ├── hot accelerator          ← RFC 0013
    │    ├── SQLite indexes
    │    └── native HashMap/CSR
    │
    ├── analytical snapshot      ← e.g. Parquet when justified
    └── interchange snapshot     ← e.g. Arrow when justified
```

The RFC 0013 requirement that callers never choose `engine=sqlite`, `engine=hashmap`, or another physical backend remains unchanged. Likewise, RFC 0012 continues to forbid feature-specific semantic scanners or indexes.

## First evidence: SQLite hot target

The first implemented target materializes canonical `concepts` and `links` into indexed SQLite through DuckDB cross-database CTAS.

At 50,000 concepts / 100,000 links, the preserved same-host benchmark measured median hot latency approximately as follows:

| Operation | DuckDB | SQLite file | SQLite memory |
| --- | ---: | ---: | ---: |
| exact `concept_id` | 618.014 µs | 11.782 µs | 2.522 µs |
| outgoing adjacency | 1,163.033 µs | 14.283 µs | 3.978 µs |

Median DuckDB → indexed SQLite construction was 192.228 ms across three preserved repetitions. The target therefore paid for itself against repeated DuckDB point access after hundreds, not thousands, of hot queries in that fixture.

These measurements justify SQLite for this workload. They do not establish SQLite as the universal snapshot format.

## Acceptance invariants

A new physical target is acceptable only if:

1. **Canonical-only input** — construction consumes canonical relations and performs no semantic reparse.
2. **Parity** — overlapping observable data agree with the canonical relational provider.
3. **Explicit workload** — the target exists for a named operation/deployment shape, not merely because a format is available.
4. **Measured construction cost** — build/export latency is reported separately from query latency.
5. **Break-even evidence** — when acceleration is the goal, the benchmark reports when construction pays for itself.
6. **Resource evidence** — relevant memory, disk, or transfer cost is measured.
7. **Disposable lifecycle** — the target can be invalidated and rebuilt from canonical snapshot identity.
8. **No public backend semantics** — callers do not need to select or understand the physical format for ordinary relational operations.
9. **No universal-format claim** — adding one successful target does not make it the default for unrelated workloads.
10. **No speculative API surface** — unimplemented target classes remain architectural possibilities, not callable product contracts.

## Consequence

The project should optimize by asking:

> **What physical representation best serves this workload, and can DuckDB derive it cheaply from the canonical relation snapshot?**

—not:

> Which single database should own every representation of OKF?
