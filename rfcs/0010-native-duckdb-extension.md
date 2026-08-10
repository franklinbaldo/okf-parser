---
type: RFC
title: Native DuckDB extension for OKF bundles
status: proposed
description: Expose OKF directories as optimizer-aware DuckDB table functions backed by the shared Rust semantic engine
---

# RFC 0010: Native DuckDB extension for OKF bundles

## Summary

This RFC proposes a loadable DuckDB extension named `okf`, implemented in
Rust and maintained in the `okf-parser` repository. It exposes an OKF
directory as native DuckDB table functions:

```sql
INSTALL okf FROM community;
LOAD okf;

FROM okf_concepts('./knowledge');
FROM okf_links('./knowledge');
FROM okf_reserved('./knowledge');
FROM okf_diagnostics('./knowledge');
```

The extension does not replace the CLI, Python package or TypeScript package.
It removes an avoidable boundary for consumers whose terminal operation is
already SQL: filesystem discovery, parsing and validation run in the shared
Rust engine, and records are emitted directly as DuckDB vectors/data chunks
without a subprocess or whole-bundle JSON document.

The semantic engine remains independent of DuckDB. The repository is organized
as a Cargo workspace with three responsibilities:

```text
okf-engine         OKF semantics and bounded ingestion; no DuckDB dependency
okf-core           standalone CLI and compatibility JSON/direct database surfaces
duckdb-extension   loadable DuckDB adapter over okf-engine
```

The first RFC is read-only. SQL-to-Markdown mutation remains governed by the
existing preview/write and optimistic-conflict contracts and requires a
separate RFC.

Tracking issue: #113.

## Motivation

### The current adapter is fast but still terminally indirect

Version 0.38.0 moved discovery, bounded parallel reads, YAML/frontmatter,
Markdown facts, validation, link resolution and content digests into Rust.
Same-host warm-cache measurements for 100,000 small documents were:

| Surface | Time |
| --- | ---: |
| Rust JSON CLI | 1.056 s |
| Rust direct DuckDB materialization | 1.557 s |
| TypeScript through Rust JSON adapter | 2.603 s |
| Python/Ibis through Rust JSON adapter | 7.711 s |

The adapters are useful portable integration surfaces, but a DuckDB consumer
currently follows this path:

```text
DuckDB client
  -> Python or TypeScript
  -> Rust subprocess
  -> complete JSON bundle
  -> language object projection / Ibis memtables
  -> DuckDB
```

A DuckDB extension permits:

```text
DuckDB
  -> Rust table function
  -> okf-engine
  -> DuckDB vectors
```

The expected improvement is not merely subprocess startup. It removes complete
JSON serialization/decoding, duplicate relational construction and the need to
materialize columns and capabilities that the query does not request.

### DuckDB can become the query planner

The DuckDB C table-function API has explicit bind, init, local-init and
execution stages. It supports maximum thread declaration and projection
pushdown: init receives the indexes of requested result columns. That matches
the Greenfield ingestion design already adopted by this project.

For example:

```sql
SELECT concept_id, title
FROM okf_concepts('./knowledge')
WHERE concept_type = 'Note';
```

must not require retaining bodies or serializing complete frontmatter merely
because those columns exist in the canonical relation.

References:

- <https://duckdb.org/docs/current/clients/c/table_functions.html>
- <https://github.com/duckdb/duckdb-rs>
- <https://github.com/duckdb/extension-template-rs>
- <https://duckdb.org/community_extensions/>

## Decision

### 1. The extension stays in this repository

The MVP lives in `franklinbaldo/okf-parser` as part of one Cargo workspace.

This keeps:

- one semantic implementation;
- one conformance corpus;
- synchronized relation and digest contracts;
- cross-runtime equivalence in one CI;
- atomic review when semantics affect every consumer.

A later distribution-only repository may be introduced if DuckDB Community
Extensions requires or materially benefits from one. Such a repository must
remain a thin build descriptor over released source; it cannot fork semantics.

### 2. OKF semantics move to a DuckDB-independent crate

`okf-engine` owns:

- safe recursive discovery and exclusion semantics;
- bounded filesystem admission and reads;
- UTF-8 and newline handling;
- failsafe YAML/frontmatter parsing;
- Markdown headings and links;
- reserved-document validation;
- concept and link identity;
- source and parsed digests;
- structured document diagnostics;
- deterministic semantic records.

It must not depend on DuckDB, Ibis, Python, Node or a subprocess protocol.

The current complete `BundleData` may remain as a compatibility collector,
but the primary internal boundary becomes incremental records/chunks plus
explicit requested capabilities. Otherwise the extension would merely hide the
same complete-bundle allocation behind an FFI call.

### 3. The extension uses the stable C Extension API from Rust

The extension targets DuckDB's C Extension API and Rust loadable-extension
tooling. It must not link against DuckDB's unstable internal C++ structures.

The extension binary links the OKF engine and its parsing dependencies. It must
not embed or statically link a second DuckDB engine into the host process.

The Rust extension template and `duckdb-rs` loadable-extension support are
still evolving. The implementation may use a narrowly reviewed local wrapper
where required, but all DuckDB interaction must remain behind a small adapter
module so tooling can change without touching OKF semantics.

### 4. Canonical SQL surface consists of four table functions

#### `okf_concepts`

```sql
FROM okf_concepts(
    './knowledge',
    exclude = [],
    read_concurrency = 32
);
```

Initial columns match the canonical concept relation:

| Column | Type |
| --- | --- |
| `concept_id` | `VARCHAR` |
| `logical_key` | `VARCHAR` |
| `path` | `VARCHAR` |
| `concept_type` | `VARCHAR` |
| `title` | `VARCHAR` nullable |
| `description` | `VARCHAR` nullable |
| `source_digest` | `VARCHAR` |
| `parsed_digest` | `VARCHAR` |
| `frontmatter_json` | `VARCHAR` |
| `body` | `VARCHAR` |

#### `okf_links`

| Column | Type |
| --- | --- |
| `source_id` | `VARCHAR` |
| `raw_target` | `VARCHAR` |
| `target_id` | `VARCHAR` nullable |
| `exists` | `BOOLEAN` |
| `origin` | `VARCHAR` |

#### `okf_reserved`

| Column | Type |
| --- | --- |
| `path` | `VARCHAR` |
| `filename` | `VARCHAR` |
| `body` | `VARCHAR` |

#### `okf_diagnostics`

| Column | Type |
| --- | --- |
| `code` | `VARCHAR` |
| `severity` | `VARCHAR` |
| `path` | `VARCHAR` |
| `message` | `VARCHAR` |

These are the canonical read surfaces. Convenience views, macros or attach
helpers delegate to them and cannot acquire separate semantics.

### 5. Configuration is explicit and query-local

The first positional parameter is the bundle root. Initial named parameters
are:

- `exclude: VARCHAR[] = []`;
- `read_concurrency: INTEGER = 32`.

The accepted range remains 1 through 256 unless measurement justifies a
different shared engine contract.

The root is resolved when the function binds/initializes under the permissions
and current filesystem view of the DuckDB process. No process-global default
root or mutable global exclusion state is introduced.

A later remote-filesystem adapter is possible, but local filesystem semantics
must not be silently generalized to URLs in this RFC.

### 6. Document diagnostics are rows; fatal scan failures are SQL errors

The established bundle contract isolates invalid individual documents and
emits deterministic diagnostics. The extension preserves that distinction.

Examples that remain rows in `okf_diagnostics`:

- invalid concept frontmatter;
- missing/non-string concept type;
- malformed reserved document;
- unresolved local Markdown link.

Examples that fail the table function:

- root does not exist or is not a directory;
- invalid function parameter;
- exclusion configuration cannot be compiled;
- root-level discovery cannot proceed;
- allocation or internal invariant failure.

A nonconformant bundle is still queryable. A scan that never established a
coherent bundle boundary is not represented as a synthetic document row.

### 7. Each invocation observes one coherent scan snapshot

One table-function invocation has one discovery result and one set of known
paths. Link existence and target identity use that coherent set.

The extension does not promise a filesystem transaction across independent SQL
statements. Calling `okf_concepts(root)` and then `okf_links(root)` may
observe changes made between statements.

No process-global cache is normative. Any future cache must be explicit,
bounded, invalidatable and tested for freshness. Correctness cannot depend on
mtime granularity.

### 8. Projection pushdown maps to engine capabilities

The extension advertises projection pushdown and reads the requested column
indexes during init.

At minimum:

- selecting identity/path/type/title does not retain `body`;
- omitting `frontmatter_json` does not serialize it;
- omitting source or parsed digest avoids the corresponding hash when no other
  capability needs it;
- querying concepts does not compute links or reserved rows;
- querying reserved rows does not parse ordinary concepts beyond what coherent
  discovery requires;
- diagnostics compute only validation capabilities required by that relation.

This is capability pushdown inside the engine, not only omission when writing
the final DuckDB vector.

Filter pushdown is not claimed by the first RFC because the stable C
table-function surface used here explicitly provides projection pushdown but
does not by itself make every SQL predicate available to the extension.
Ordinary DuckDB filtering remains correct. A later implementation may add
supported filter pushdown without changing relation semantics.

### 9. Execution is chunked, bounded and parallel

The table function emits DuckDB-sized data chunks. It never constructs one
complete JSON or Arrow bundle before returning the first chunk.

The engine/adapter pipeline has bounded stages:

```text
discovery -> admitted reads -> parse/validate -> ordered semantic records -> data chunks
```

Bounds apply to:

- active filesystem reads;
- parse workers;
- queued paths;
- completed-but-not-emitted records;
- open file descriptors;
- retained source/body bytes.

DuckDB determines available execution parallelism through its table-function
lifecycle. The extension sets a truthful maximum and uses thread-local init
state rather than sharing one mutable scanner unsafely.

Deterministic semantics do not imply a SQL row-order guarantee. Tests may
compare canonical ordering, but public queries that require order must use
`ORDER BY`.

### 10. Cancellation stops admission and releases resources

When DuckDB interrupts or abandons a query, the extension must stop admitting
new filesystem work as soon as the supported extension API permits, close
resources and drop queued outputs.

The implementation must test cancellation behavior rather than infer it from
Rust object destruction. If the stable API lacks a direct interruption probe,
the first implementation checks between chunk/work units and documents the
maximum cooperative latency; it must not reach into unstable DuckDB internals
to manufacture stronger semantics.

### 11. `okf_attach` is convenience, not eager materialization

A later slice may provide:

```sql
CALL okf_attach('./knowledge', schema = 'knowledge');

SELECT * FROM knowledge.concepts;
SELECT * FROM knowledge.links;
SELECT * FROM knowledge.reserved;
SELECT * FROM knowledge.diagnostics;
```

The supported implementation may be a procedure, pragma or macro helper,
depending on what the stable extension API exposes. It creates lazy views or
equivalent queryable bindings over the canonical table functions.

It does not copy the whole bundle into persistent tables by default.

It must define identifier quoting, literal quoting, collision behavior,
replacement policy and connection lifetime before landing. Multiple roots may
coexist under distinct schemas.

### 12. Python and TypeScript remain supported surfaces

The extension does not make Python or TypeScript obsolete.

They remain appropriate for:

- environments where extensions cannot be loaded;
- application/service APIs;
- mutation workflows;
- MCP;
- formatting and generated contracts;
- portable fallback.

Consumers already using DuckDB should be able to select the extension without
knowing a `rust_core` executable path. Package-level automatic engine
selection remains a separate deployment concern tracked by #105 and #106.

### 13. Distribution targets DuckDB Community Extensions

Development begins with unsigned CI artifacts loadable in a compatible stock
DuckDB development invocation.

The target user experience is:

```sql
INSTALL okf FROM community;
LOAD okf;
```

Community submission occurs only after:

- reproducible builds;
- compatible target matrix;
- shared conformance tests;
- version/API compatibility policy;
- security review of filesystem authority;
- 1M-file resource measurements.

Community extensions execute with the privileges of the DuckDB process.
Documentation must state that loading the extension authorizes local
filesystem reads requested by SQL under those privileges.

### 14. Versioning follows one semantic release identity

The extension, CLI and adapters share the OKF semantic protocol version when
they ship from this repository.

Extension binary compatibility also depends on the targeted DuckDB extension
API/version. Build metadata and release manifests therefore identify both:

- OKF semantic version/source commit;
- DuckDB extension API/target compatibility.

This composes with #107. A compatible extension must fail clearly when loaded
by an unsupported DuckDB rather than producing undefined behavior.

## Rejected alternatives

### Keep only the direct `okf-core duckdb` materializer

Rejected as the only SQL surface. It is useful for persistent snapshots but
requires eager complete ingestion before arbitrary queries and cannot benefit
from projection pushdown.

### Implement the extension in C++ by copying the Rust engine

Rejected. It creates two semantic authorities and repeats the divergence the
Rust promotion was intended to remove.

### Put DuckDB inside `okf-engine`

Rejected. The semantic engine must remain reusable by the CLI and adapters
without carrying a database dependency or extension lifecycle.

### Return one generic relation with a `kind` discriminator

Rejected for the canonical API. Concepts, links, reserved documents and
diagnostics have different schemas and capabilities. A union-shaped relation
would be sparse, weaken typing and force irrelevant work.

### Materialize four tables automatically on `LOAD`

Rejected. Extension loading must not scan an arbitrary directory or mutate a
database. Roots are explicit query inputs.

### Promise transparent cache reuse across all four functions

Rejected initially. Safe freshness and lifetime semantics are harder than the
scan itself. Independent correct scans are preferable to an invisible stale
cache.

### Implement writes in the first extension RFC

Rejected. DuckDB relational mutation back into authored Markdown needs preview,
lossless round-trip, optimistic conflicts and effect-aware authorization. The
existing RFC 0005/0008 contracts remain authoritative until a dedicated
extension-write RFC reconciles them.

### Move immediately to a separate repository

Rejected for the MVP. It would introduce version and conformance coordination
before the extension boundary has stabilized.

## Implementation sequence

1. #114 — extract `okf-engine` without changing behavior.
2. #115 — scaffold the loadable extension and land `okf_concepts()`.
3. #116 — add links, reserved documents and diagnostics.
4. #117 — implement projection/capability pushdown, chunks, parallelism,
   backpressure and cancellation.
5. #118 — add lazy attach/view convenience.
6. #119 — measure 10K/100K/1M and prepare Community distribution.

## Acceptance criteria

RFC 0010 can move from `proposed` to `accepted` when the project agrees that:

- the extension belongs in this repository for the MVP;
- OKF semantics live in a DuckDB-independent Rust crate;
- the extension uses the stable C Extension API from Rust;
- four typed table functions are the canonical SQL surface;
- document nonconformance remains queryable as diagnostics;
- fatal scan/configuration failures are SQL errors;
- one invocation has one coherent discovery snapshot;
- projection pushdown reaches engine capabilities;
- execution is chunked, bounded and parallel;
- cancellation stops admission within the supported stable API contract;
- attach is lazy convenience over canonical functions;
- Python, TypeScript and CLI fallbacks remain supported;
- Community Extension distribution is the target after scale/security gates;
- mutation is explicitly deferred to a separate RFC.

Implementation completion is separate from acceptance.
