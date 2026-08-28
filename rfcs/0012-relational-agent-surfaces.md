---
type: RFC
title: Relational agent surfaces from OKF Generator benchmark
status: proposed
description: Adopt useful agent-facing product semantics from okf-generator over one canonical relational read service, preserving okf-parser's authored-OKF, DuckDB/Ibis, TypeContract, native-engine, and projection-first architecture.
---

# RFC 0012: Relational agent surfaces from OKF Generator benchmark

## Summary

`okf-generator` demonstrates several useful agent-facing product ideas: concept
lookup, relationship navigation, semantic diff with impact analysis, compact
context, agent installation helpers, MCP access, and incremental refresh.

`okf-parser` should adopt those **product semantics** without importing the
benchmark's code-oriented AST architecture, code ontology, storage model, or
implementation code.

The architectural rule of this RFC is:

```text
authored OKF / explicit source adapters
        ↓
strict parse + canonical semantic engine
        ↓
canonical relations
  concepts / links / reserved / diagnostics
        ↓
shared relational read service
  native RFC 0010 table functions when available
  Python/Ibis fallback with the same observable contract
        ↓
TypeContract / typed projections where requested
        ↓
query / lookup / relations / diff / impact / context
        ↓
CLI / MCP / agent integrations
```

The **shared relational read service**, not `query --sql`, is the foundational
milestone. `query` is one consumer of that service. Structured `lookup` is
another and must construct relational/Ibis predicates directly rather than
building SQL strings.

NetworkX remains a projection for graph algorithms. Persistent DuckDB remains a
disposable compiled image or interoperability snapshot, not a second source of
truth. Agent JSON/text/MCP payloads are serializations of relational results,
not independent semantic models.

This RFC is proposal-only. The pre-RFC agent feature stack is abandoned; no
feature PR from that stack was opened.

## Motivation

### The benchmark exposed a real product gap

The public `okf-generator` workflow makes knowledge inexpensive for an agent to
inspect incrementally instead of repeatedly scanning a corpus. The useful
product ideas are:

- exact and filtered concept lookup;
- relationship navigation;
- `diff --impact`;
- compact context with an explicit budget;
- one-command agent installation;
- MCP tools over the same knowledge model;
- incremental refresh based on deterministic change detection.

Those experiences do not require tree-sitter, Function/Class/Module semantics,
a generator-specific bundle dialect, or a second knowledge store.

Reference: <https://github.com/UmairBaig8/okf-generator>

### `okf-parser` already has a different architectural center

The repository architecture already says:

```text
discovery / classification
        ↓
strict authored OKF parse
        ↓
authoritative normalized relations
        ↓
TypeContract
        ↓
typed DuckDB / Ibis relations
        ↓
consumer projections and adapters
```

That is the project's identity, not an implementation accident. The core
answers what canonical OKF says. External dialects are adapted before downstream
consumers see them.

RFC 0005 makes concept types relational. RFC 0006 makes DuckDB catalog/type
semantics part of compilation. The Python API already exposes Ibis relations,
`attach_okf()` persists ordinary relational tables, and NetworkX is already a
projection of `concepts` + `links`.

The missing capability is therefore a coherent **read service and agent product
surface over the relational model already owned by the project**.

### Issue #151 identified the first consumer, not the substrate

Issue #151 measured a concrete ergonomics problem: read-only structured access
currently requires first exporting DuckDB and then opening a second connection.
It proposes a `query` sibling to `apply`.

The important correction in this RFC is that `query` is not itself the
foundation. The foundation is the shared relational read service that presents
the canonical relations. `query --sql` and structured agent operations are
siblings above it.

Reference: <https://github.com/franklinbaldo/okf-parser/issues/151>

## Relationship to RFC 0010: one relational contract, two execution backends

RFC 0010 proposes a native DuckDB extension backed by the shared `okf-engine`,
with four canonical SQL table functions:

```sql
okf_concepts(root)
okf_links(root)
okf_reserved(root)
okf_diagnostics(root)
```

Those functions are the preferred native SQL backend for this RFC when the
extension is available. They are **not** a separate semantic universe beside
this RFC's read service.

The service contract is the four canonical relations and their semantics. It
may be fulfilled by:

1. the RFC 0010 native table functions backed by `okf-engine`; or
2. the portable Python/Ibis path backed by the existing canonical parser/engine
   boundary when the extension is unavailable.

The two backends must be observably equivalent for the same authored bundle,
configuration, and requested capabilities. Backend selection is deployment and
performance policy; it must not change identity, digests, link resolution, or
diagnostics.

This prevents two competing materializations:

```text
                         ┌─ RFC 0010 native table functions ─┐
authored bundle → canonical semantic contract               ├→ read services
                         └─ portable Python/Ibis fallback ───┘
```

A third feature-specific scanner/index is not allowed. In particular, `lookup`
must not bypass this boundary by walking Markdown/`ConceptRecord` objects on its
own.

RFC 0010 remains independently reviewable: this RFC does not require every host
to load a native extension. If RFC 0010 is absent, the fallback still implements
the same logical read contract. If RFC 0010 is present, `query` should use its
table functions rather than rematerializing the same bundle through an
independent SQL path.

## Normative vocabulary

### Canonical relations

The base read contract consists of `concepts`, `links`, `reserved`, and
`diagnostics`, with the same observable fields and semantics already exposed by
the repository/RFC 0010.

Typed TypeContract relations are optional projections layered on top when a
consumer explicitly requests declared-type compilation.

### Concept identity

`diff`, lookup resolution, relation navigation, and impact use the canonical
`concept_id` emitted by the active source adapter/semantic engine. Identity is
**not inferred from content equality**.

For authored filesystem OKF, the shipped contract derives `concept_id` from the
bundle-relative path without the Markdown suffix. `logical_key` is currently the
same value. Therefore, in v1:

```text
notes/a.md → notes/a
notes/b.md → notes/b
```

Renaming `notes/a.md` to `notes/b.md` is **removed + added**, even when the
semantic content and `parsed_digest` are identical. A consumer may report a
same-digest pair as an advisory `possible_move`, but that heuristic cannot
collapse the two identities or change diff semantics.

A future source adapter may define another canonical identity under its own RFC
(for example a qualified Git object identity). `diff` consumes the provider's
canonical `concept_id`; it never guesses identity from title, body, or digest.

### `source_digest` and `parsed_digest`

This RFC reuses the repository/RFC 0009 vocabulary exactly:

- `source_digest` identifies the normalized exact authored source representation
  governed by the parser's source-digest contract;
- `parsed_digest` identifies the canonical parsed semantic projection governed
  by the parser's parsed-digest contract.

A diff may report both. It must not invent a generic `content_digest` whose
meaning overlaps either one.

### Compiled image

A compiled image is any persistent/read-optimized derivative, including a
DuckDB snapshot or future cache. It is never authored state and may always be
deleted and regenerated.

## Design principles

### 1. Authored OKF remains canonical

Markdown/frontmatter/authored links, or the canonical representation produced
by an explicit source adapter, remain authoritative. No agent feature requires
editing an index database instead of the source corpus.

### 2. One relational semantic path

`query`, `lookup`, `related`, `diff`, `impact`, and `context` consume the shared
relational read service. Native and portable execution backends must satisfy the
same relation contract.

No feature-specific parser, `ConceptRecord` index, graph store, or bespoke
Markdown scan is an acceptable shortcut.

### 3. DuckDB/Ibis is the ordinary query substrate; NetworkX is a projection

Filtering, projection, joins, aggregation, typed access, and relational
comparison belong in DuckDB/Ibis. Graph algorithms may use NetworkX or recursive
SQL, but their nodes/edges come from canonical relations.

### 4. Domain semantics stay outside the generic core

The generic vocabulary is concept, type, field, link, incoming, outgoing,
related, diagnostic. `Function`, `Class`, `caller`, `callee`, `import`, and
similar terms require a source adapter/profile that proves those semantics.

### 5. Shared services, thin transports

CLI and MCP call the same public service functions. MCP does not get a second
lookup implementation, and CLI raw SQL is not the implementation language of
structured lookup.

When a result is naturally tabular, the internal contract stays relational.
JSON/text are transport projections rather than a new semantic dialect.

### 6. Derived artifacts are disposable and completely keyed

A reusable compiled image/cache must include every input that can change its
relations, at least:

- authored source identity/digests;
- parser/compiler semantic version;
- **execution backend/engine identity** (`python`, `okf-engine`, native
  extension, including a version/source identity sufficient to distinguish
  builds);
- exclusion configuration;
- TypeContract/spec-template configuration;
- requested capabilities/projection policy when it changes stored facts;
- every other option that affects relation contents.

A cache produced by the Rust/native path must never be accepted merely because
its filesystem inputs match a cache expected from the Python path. Backend
parity is tested, not assumed as cache identity.

Deletion of the compiled image must always be a correct recovery operation.

### 7. Deterministic and offline by default

No capability in this RFC requires embeddings, a vector database, network
access, or an LLM. Optional semantic retrieval/enrichment belongs to a later
consumer or adapter.

### 8. Copy product semantics, not implementation assumptions

The benchmark informs requirements and UX only. Implementation comes from this
repository's semantic engine, canonical relations, DuckDB/Ibis, TypeContract,
graph projection, service layer, and RFC 0008 MCP conventions.

## Benchmark disposition

| `okf-generator` capability | Decision | `okf-parser` interpretation |
| --- | --- | --- |
| lookup / get concept | adopt | structured predicates over canonical relations |
| type/tag filters | adopt generically | type/field predicates; no code taxonomy |
| fuzzy symbol search | defer | ranked suggestions only; never identity resolution |
| callers / callees | adapt | incoming/outgoing/related; aliases only in code adapters |
| diff | adopt | relational comparison using canonical identity + shipped digests |
| diff --impact | adopt | cycle-safe reachability over canonical links |
| compact context | adopt | deterministic budgeted projection generated on demand |
| SUMMARY.md | do not require | derive context; avoid committed duplicate state |
| agent install | adopt | minimal rules pointing at read/diff/check surfaces |
| MCP lookup tools | adopt | thin wrappers over shared services |
| incremental update | adapt | disposable compiled image keyed by full compiler/backend identity |
| tree-sitter extraction | reject from core | source-adapter concern |
| Function/Class/Module ontology | reject from core | domain-specific taxonomy |
| LLM ask/enrichment | reject from core | optional downstream concern |
| training pairs | reject from core | downstream dataset tooling |
| dashboard | reject from core for now | consumer UI |

## Decision

### 1. The first milestone is the shared relational read service

Before `query` or `lookup`, implementation must define one service boundary that
opens canonical relations for a bundle/configuration.

Conceptually:

```text
open_relations(bundle, options)
    → concepts
    → links
    → reserved
    → diagnostics
    → optional typed relations
```

The public type/name may differ. The requirement is architectural: consumers do
not decide how Markdown is scanned, how the native engine is selected, or how
canonical rows are constructed.

Milestone 1a is implementable today: define the relation-provider/service
boundary with the portable canonical fallback and a language-neutral conformance
corpus that pins the four relation contracts.

Milestone 1b is conditional on RFC 0010 being accepted and available: wire the
native table-function provider to the same boundary and run the same corpus as a
backend-parity test. RFC 0012 acceptance and milestone 1a do not depend on the
native extension having landed first.

### 2. `query --sql` is a trusted power-user consumer

Issue #151 becomes the first user-visible consumer:

```text
okf-parser query <bundle> --sql "SELECT ..."
```

It opens the shared relation service, executes the requested read query, and
returns stable tabular output.

"SELECT-only" is a mutation-shape rule, not a sandbox claim. DuckDB table
functions can perform external I/O. RFC 0006 already rejects fake SQL sandboxing.
Therefore raw SQL is a trusted power-user capability and any MCP exposure would
need honest RFC 0008 annotations for its maximum legal effect.

### 3. Structured `lookup` is a sibling consumer, not SQL generation

The first version should support deterministic forms such as:

```text
okf-parser lookup <concept-id-or-path>
okf-parser lookup --type <type>
okf-parser lookup --field status=aberta
```

The shared lookup service builds Ibis/relational expressions or equivalent
structured predicates against canonical relations. It must **not** implement
lookup by string-concatenating SQL for `query --sql`.

CLI and MCP both call that service. Raw SQL is unnecessary for ordinary MCP
lookup.

Fuzzy retrieval, if later added, returns explicitly ranked suggestions and
never silently resolves canonical identity.

### 4. Relation navigation is generic

The read service exposes operations equivalent to:

```text
incoming(concept)
outgoing(concept)
related(concept)
```

They use canonical `links`. Code-specific `callers`/`callees` aliases belong to
a code adapter/profile.

### 5. `diff` is relational and identity-preserving

`diff(base, head)` compares canonical relation snapshots by `concept_id`.
Minimum output includes:

- concepts added/removed;
- for surviving identities, `source_digest_changed` and
  `parsed_digest_changed` separately;
- fields added/removed/changed where represented by the compiled relation;
- links added/removed;
- diagnostics introduced/resolved.

A textual Markdown diff is complementary evidence, not the semantic contract.
A rename in authored filesystem OKF is removed+added under the identity rule
above; digest equality may only produce advisory move evidence.

### 6. `impact` is cycle-safe deterministic reachability

`diff --impact` starts from changed concept identities/edges and computes a
relational neighborhood over canonical links. It reports reachability, **not
causation**.

Default traversal for "what may depend on this change" follows incoming links
(reverse edge direction) with an explicit depth bound. Consumers may request
outgoing or both directions.

Traversal is defined over the union of link identities from both snapshots,
`E_base ∪ E_head`, so a removed edge remains visible through `base` and an added
edge is visible through `head`. Every traversed edge carries snapshot presence:
`base`, `head`, or both.

The union is a discovery surface, not permission to invent a path that never
existed. Each frontier state carries a path-support set initialized from the
seed's valid snapshot(s). Crossing an edge intersects that set with the edge's
snapshot presence. A state whose support becomes empty is discarded. Therefore
an edge that exists only in `base` cannot be concatenated with a later edge that
exists only in `head` and reported as one historical path.

Reported impact preserves snapshot provenance for the accepted path (or paths):
which snapshot(s) support the reachability and, when path detail is requested,
the snapshot presence of each edge. A concept reachable in both snapshots may
still be emitted once at minimum depth while aggregating the supported snapshot
set deterministically.

The traversal must:

- maintain cycle-safe visited state keyed by at least concept identity plus
  snapshot-support state and terminate on cycles;
- emit each impacted `concept_id` once at its minimum discovered depth, with
  deterministic aggregation of supported snapshots when equal-depth paths exist;
- use deterministic frontier ordering by canonical `concept_id` and snapshot
  support;
- produce stable final ordering by `(depth, concept_id)`;
- never depend on NetworkX insertion order, SQL incidental row order, or worker
  completion order.

DuckDB recursive CTEs, NetworkX, or another graph implementation are acceptable
if they satisfy that contract over the base/head link snapshots from the same
relational service.

### 7. `context` has explicit deterministic budgets

`context <concept>` composes identity/type/path, selected fields, diagnostics,
relations, and optionally neighbor summaries from current relations.

The core stays tokenizer-independent. Its default hard bounds are:

```text
max_bytes      = 16384   # UTF-8 serialized output budget
max_relations  = 50      # included relation/neighbor records
depth          = 1
```

A caller may request different explicit values. Expansion order is deterministic
by relation direction, depth, and canonical `concept_id`. If the byte budget is
reached, truncation occurs only at record/body-fragment boundaries and the
result reports `truncated=true` plus the effective limits.

Agent integrations that know a model tokenizer may translate their token budget
into a stricter byte budget before calling the service. The core must not claim
a universal token count without naming a tokenizer/model.

A committed `SUMMARY.md` is not required. Context is a disposable projection.

### 8. Persistent DuckDB and incremental caches are compiled images

The distinction remains:

```text
authored bundle       = canonical source
canonical relations   = semantic read contract
DuckDB/cache image    = disposable compiled snapshot
agent JSON/context    = transport projection
```

Before introducing a new cache manifest, implementation must profile the real
read workloads. Existing `source_digest`/`parsed_digest` should be reused for
change detection where valid, but cache identity also includes the execution
backend/engine and compilation configuration from principle 6.

Deleting the compiled image and rebuilding must produce canonical-equivalent
relations and identical deterministic consumer output.

### 9. Agent installation teaches the shared surfaces

A future installer may create minimal instructions for Claude, OpenCode, Codex,
Cursor, or similar agents. The guidance should prefer:

```text
before broad corpus scanning       → lookup / context
for ad-hoc trusted relational work → query
before changing a concept          → incoming / outgoing / related
when reviewing knowledge changes   → diff --impact
after changing authored OKF        → check
```

It must not generate a second knowledge bundle or domain ontology. CLI is
sufficient; MCP is an optional transport over the same services.

### 10. MCP exposes structured operations

The ordinary MCP surface should use high-level operations such as:

```text
lookup
get_concept
incoming
outgoing
related
context
bundle_diff
impact
```

Each tool is a thin wrapper over the same service used by CLI. Raw SQL is not a
prerequisite for MCP agent UX.

## Relationship to RFC 0011 tracked migrations

RFC 0011's migration ledger is ordinary OKF authored state. If RFC 0011 lands,
its `Migration` concepts therefore appear in canonical relations and can appear
in `diff` like any other concepts.

`diff` reports the relational changes observed before/after a migration, but it
must not infer that a migration **caused** arbitrary changed concepts merely
because the ledger entry exists. Causal/audit correlation requires explicit
links/provenance or a later consumer contract.

## Non-goals and rejected architecture

This RFC does not turn `okf-parser` into an AST indexer, add language parsers,
embeddings/vector search, LLM QA, a dashboard, training-data export, or a new
binary `.okf` format.

The following approaches are specifically rejected:

- **Direct `ConceptRecord` scanning as the agent core** — bypasses the shared
  relational service.
- **Graph-first authority** — NetworkX is a projection, not the source of
  semantic facts.
- **Code ontology in generic core** — Function/Class/Module/caller/callee are
  source-domain semantics.
- **Required committed summary/index documents** — duplicate derivable state.
- **A new bespoke index/cache before measurement** — DuckDB/Ibis/native engine
  must be exercised first.
- **SQL-string generation as structured lookup** — couples agent APIs to a raw
  SQL transport and defeats the shared typed service boundary.

RFC 0005 mutation semantics, RFC 0006 declaration trust, and RFC 0008 effect
annotations remain unchanged.

## Implementation order after acceptance

Implementation starts fresh from the accepted architecture:

1a. **Portable shared relational read service** — canonical relation-provider
    boundary + portable fallback + language-neutral conformance corpus. This is
    independently implementable before RFC 0010 lands.
1b. **Native provider parity** — once RFC 0010 is accepted/available, wire its
    table functions to the same service and run the same conformance corpus
    against native and portable providers.
2. **`query` consumer** — resolve #151 over that service.
3. **Structured lookup + generic relation navigation** — service first, then
   CLI/MCP adapters.
4. **Relational diff** — identity, shipped digests, fields, links, diagnostics.
5. **Cycle-safe snapshot-aware impact** — deterministic bounded reachability
   over base/head relation snapshots without cross-snapshot synthetic paths.
6. **Budgeted context** — hard byte/relation/depth contracts.
7. **Agent installation helpers** — minimal instructions over stable surfaces.
8. **Incremental compiled-image optimization** — only after profiling proves
   benefit.

Every implementation PR must identify the layer it changes:

```text
source adapter / authored parse
canonical semantic engine
canonical relation provider
consumer service
transport adapter
compiled-image optimization
```

A PR that introduces the same semantic fact through two layers or cannot name
its layer is an architecture regression.

## Acceptance invariants

RFC acceptance is architectural; implementation completion remains separate.
The following are the conformance requirements future implementation PRs must be
able to test.

1. **Relation contract first; backend parity when native exists.** The portable
   provider must pass a language-neutral corpus that pins canonical `concepts`,
   `links`, `reserved`, and `diagnostics` with explicit deterministic ordering.
   Once RFC 0010 is accepted/available, the native provider must pass that same
   corpus and yield canonical-equal relations. RFC 0012 acceptance does not
   require a native provider that has not landed yet; parity becomes mandatory
   when that backend exists.

2. **No agent-side parse bypass.** Modules implementing `lookup`, relation
   navigation, `diff`, `impact`, or `context` do not import/use
   `parse_document`, filesystem discovery, or `ConceptRecord` to reconstruct
   canonical identity/relations. They consume the relation-provider/service
   boundary.

3. **Shared transport semantics.** CLI `lookup` and MCP `lookup` delegate to the
   same public lookup service; equivalent inputs over the same fixture produce
   equivalent normalized records.

4. **Identity is test-pinned.** Renaming an authored `a.md` to `b.md` with
   identical semantic content produces one removal and one addition. Equal
   `parsed_digest` cannot silently convert that into one unchanged concept.

5. **Digest vocabulary is preserved.** A source-only change and a semantic
   parsed change have fixtures proving distinct `source_digest_changed` versus
   `parsed_digest_changed` reporting.

6. **Impact is snapshot-aware and deterministic.** Fixtures cover a removed
   base-only edge, an added head-only edge, a cycle, and a tempting mixed path
   whose consecutive edges never coexist in one snapshot. Traversal preserves
   removed/added reachability, rejects the synthetic cross-snapshot path,
   terminates, emits each concept once at minimum depth, records supported
   snapshot provenance, and produces stable `(depth, concept_id)` ordering.

7. **Context obeys hard bounds.** Default context never exceeds 16,384 UTF-8
   bytes, includes at most 50 relation/neighbor records at depth 1, and reports
   truncation deterministically when the fixture exceeds a bound.

8. **Compiled images are disposable.** Running a deterministic consumer,
   deleting the compiled image/cache, rebuilding, and rerunning produces the
   same normalized output.

9. **Backend identity invalidates cache.** A cache key/manifest created for one
   engine/backend identity cannot validate as the other backend merely because
   source digests and configuration match.

10. **Structured lookup is not SQL text construction.** The lookup service
    accepts structured predicates/arguments and composes relational/Ibis
    expressions (or an equivalent typed relational plan); it does not build a
    raw SQL string and route it through `query --sql`.

These invariants give later reviews an objective architecture gate rather than
an instruction to merely "agree" with this RFC's prose.

## References

- `docs/architecture.md` — strict core / source-adapter boundary
- RFC 0005 — relational frontmatter writes
- RFC 0006 — declared column types and DuckDB compilation
- RFC 0008 — effect-aware MCP tools
- RFC 0009 / PR #93 — `source_digest` and `parsed_digest` vocabulary across a
  source adapter
- RFC 0010 / PR #121 — native DuckDB table-function read surface
- PR #132 and PR #133 — draft RFC 0010 implementation slices
- RFC 0011 / PR #153 — tracked migrations proposal
- issue #151 — read-only relational query consumer
- `okf-generator` — <https://github.com/UmairBaig8/okf-generator>
