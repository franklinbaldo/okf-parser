---
type: RFC
title: Relational agent surfaces from OKF Generator benchmark
status: proposed
description: Adopt the useful agent-facing product ideas demonstrated by okf-generator while preserving okf-parser's authored-OKF, Ibis, DuckDB, TypeContract, and projection-first architecture.
---

# RFC 0012: Relational agent surfaces from OKF Generator benchmark

## Summary

`okf-generator` demonstrates several agent-facing product ideas that are useful
independently of its code-oriented AST architecture: fast concept lookup,
relationship navigation, bundle diff with impact analysis, compact context,
agent installation helpers, MCP access, and incremental refresh.

`okf-parser` should adopt the **product semantics** of those capabilities, but
must not copy their implementation model or turn its strict OKF core into a
code-indexing system.

The architectural rule of this RFC is:

```text
authored OKF Markdown
        ↓
strict parse / validation
        ↓
authoritative normalized relations
        ↓
TypeContract
        ↓
DuckDB / Ibis relational compilation
        ↓
query / lookup / diff / impact / context
        ↓
CLI / MCP / agent integrations
```

NetworkX remains a projection for graph algorithms. It is not the canonical
store behind agent features. Persistent DuckDB remains an optional compiled
image of the bundle, not a second source of truth. Agent-facing JSON, text, or
MCP payloads are serializations of relational results, not independent semantic
models.

This RFC is proposal-only. The implementation stack attempted before this RFC
is abandoned; no feature PR from that stack was opened.

## Motivation

### The benchmark exposed a real product gap

The public `okf-generator` documentation presents a coherent agent workflow:

```text
generate → lookup → diff → visualize → mcp → agent integration
```

Its useful ideas include:

- exact and filtered concept lookup;
- related/caller/callee navigation;
- `diff --impact`;
- compact agent context rather than re-reading whole files;
- one-command agent installation;
- MCP tools over the same knowledge model;
- incremental refresh based on deterministic change detection.

Those are useful user experiences. They do not require copying tree-sitter,
function/class/module assumptions, its bundle dialect, its implementation
code, or its code-specific relationship vocabulary.

Reference:
<https://github.com/UmairBaig8/okf-generator>

### `okf-parser` already has a different and stronger architectural center

The existing architecture document defines this core pipeline:

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

That is not merely an implementation detail. It is the project's identity.

The core answers what an authored OKF bundle says. External source dialects are
adapted into canonical OKF before downstream consumers see them. The project
therefore does not need a new agent-specific semantic store.

RFC 0005 already treats concept types relationally. RFC 0006 makes DuckDB's
catalog and logical types part of the compilation model. `attach_okf()` already
materializes ordinary `concepts`, `links`, `reserved`, and `diagnostics` tables,
with optional typed tables derived from declarations. The Python API already
exposes Ibis relations. NetworkX is explicitly a projection of those relations.

The missing piece is not another index format. The missing piece is a good
**read/query layer and agent product surface over the relational model that
already exists**.

### Issue #151 already identified the correct primitive

Issue #151 measured a concrete ergonomics problem: read-only structured access
currently requires materializing a DuckDB file and reconnecting separately.
Its revised proposal is a sibling `query` command that reuses the same
materialization used by `apply`, accepts read-oriented SQL, and returns rows
without invoking write staging.

This RFC treats that work as foundational rather than creating a separate
`lookup` implementation that scans `ConceptRecord` objects directly.

Reference: <https://github.com/franklinbaldo/okf-parser/issues/151>

## Design principles

The following principles are normative for every capability introduced by this
RFC.

### 1. Authored OKF remains the source of truth

Markdown/frontmatter and authored links remain authoritative. A persistent
DuckDB database is a compiled representation. It may be regenerated or safely
discarded without losing authored knowledge.

No agent feature may require users to edit an index database instead of the
bundle.

### 2. Relational compilation comes before agent convenience APIs

`lookup`, `related`, `diff`, `impact`, and `context` should be derived from the
same normalized relations used by the rest of the project.

A feature-specific Python index, graph cache, or bespoke document scan is not a
second acceptable core path merely because it is easier to implement.

### 3. DuckDB/Ibis is the ordinary query substrate

The project should use DuckDB/Ibis for filtering, projection, joins,
aggregation, type-aware access, and relational comparison.

A command does not need a persistent `.duckdb` file to benefit from this rule:
the service may compile an in-memory relational image for one call. Persistent
DuckDB is an optimization and interoperability surface, not a prerequisite for
ordinary use.

### 4. NetworkX is a graph projection, not semantic authority

Traversal algorithms, connected components, cycle detection, shortest paths,
or transitive impact may reasonably use NetworkX when it is the clearest
implementation.

The nodes and edges supplied to those algorithms must come from the canonical
relations. Agent features must not silently create a competing graph model.

### 5. Domain semantics stay outside the generic core

The core vocabulary is generic: concept, type, incoming relation, outgoing
relation, related concept, field, link, diagnostic.

Code-specific words such as `Function`, `Class`, `caller`, `callee`, `import`,
or `dependency` belong to a source adapter or profile that can prove those
semantics. They are not generic OKF primitives.

### 6. Derived artifacts are disposable and versioned by their inputs

Any persistent cache or compiled image must be derivable from authored input
plus explicit compilation options. It must never become hidden authoritative
state.

A reusable cache must include enough identity to invalidate on at least:

- authored-content digest changes;
- parser/compiler version changes;
- exclusion configuration changes;
- type/spec compilation configuration changes;
- any other option that changes the resulting relations.

Deletion of the cache must always be a correct recovery operation.

### 7. CLI and MCP remain thin adapters over shared services

RFC 0008 established this rule for mutations. This RFC applies it equally to
reads.

There should not be a CLI lookup engine and a second MCP lookup engine. Both
must call the same relational service functions and expose equivalent semantics.

### 8. Prefer relational/tabular output over new JSON dialects

When a result is naturally rows and columns, the internal contract should stay
relational. JSON exists as a transport serialization for agents, not as a new
nested semantic schema that the core must maintain independently.

Higher-level concept/context responses may be structured for usability, but
they should be composed from stable relational records rather than inventing a
parallel object graph.

### 9. Deterministic, offline behavior is the baseline

The benchmark's deterministic/offline property is worth preserving.

No capability in this RFC requires embeddings, a vector database, or an LLM.
Optional semantic retrieval or enrichment may exist later as an adapter or
consumer, but it is not part of the first-class core defined here.

### 10. Copy product ideas, not code or architectural assumptions

This benchmark is clean-room at the product level: public behavior and user
experience inform requirements. No implementation code is copied from
`okf-generator`.

The implementation must emerge from `okf-parser`'s own parser, relations,
TypeContract, DuckDB/Ibis, graph projection, service layer, and MCP conventions.

## Benchmark disposition

| `okf-generator` capability | Decision here | `okf-parser` interpretation |
| --- | --- | --- |
| `lookup` / `get_concept` | adopt | relational lookup over canonical concept/type relations |
| type/tag filters | adopt generically | field/type predicates over compiled relations; no code taxonomy |
| fuzzy symbol search | defer from v1 | exact identity and explicit filtering first; fuzzy matching must not silently become identity resolution |
| callers / callees | adapt | generic incoming/outgoing/related; code adapters may expose caller/callee aliases |
| `diff` | adopt | relational comparison of concept identity, digests, fields, links, and diagnostics |
| `diff --impact` | adopt | changed relational identities plus transitive traversal over canonical link relations |
| compact lookup context | adopt | deterministic context projection generated on demand from relations |
| `SUMMARY.md` | do not require | derive summaries/context on demand; avoid committed duplicate state by default |
| agent `install` | adopt | install minimal rules that point agents at query/context/diff/check; CLI-first, MCP optional |
| MCP lookup tools | adopt | thin MCP wrappers over shared read services |
| incremental update | adopt principle, not mechanism | reuse existing digests and optional derived relational cache; no generator-owned manifest semantics in core |
| tree-sitter AST extraction | reject from core | belongs in an external source adapter that projects code into canonical OKF |
| Function/Class/Module ontology | reject from core | domain-specific taxonomy |
| LLM `ask` / enrichment | reject from core | optional consumer/adapter concern |
| training pairs | reject from core | downstream dataset tooling |
| FastAPI dashboard | reject from core for now | consumer UI, not parser responsibility |

## Decision

### 1. `query` is the foundational read primitive

The first implementation milestone should resolve issue #151 instead of adding
`lookup` first.

Conceptually:

```text
okf-parser query <bundle> --sql "SELECT ..."
```

The service compiles the bundle into the same relational concept tables used by
`apply`/typed compilation, executes the read query, and returns a stable tabular
result.

The exact SQL safety contract requires care. "SELECT-only" is a **mutation
shape rule**, not a claim that arbitrary DuckDB SQL is sandboxed: DuckDB table
functions can still access external resources. RFC 0006 already rejects fake
SQL sandboxing. Therefore:

- the CLI may expose explicit raw read SQL as a trusted power-user operation;
- MCP should not need raw SQL merely to support ordinary agent lookup;
- high-level agent tools should compile safe structured predicates into the
  relational service rather than asking the agent to construct arbitrary SQL;
- any raw-SQL MCP exposure must use honest RFC 0008 effect annotations based on
  its maximum legal effect.

### 2. First-class `lookup` is a convenience projection over `query`

`lookup` should not scan Markdown or build its own index.

The first version should support deterministic forms such as:

```text
okf-parser lookup <concept-id-or-path>
okf-parser lookup --type <type>
okf-parser lookup --field status=aberta
```

The service resolves these against canonical relational columns and returns
stable concept records.

Fuzzy search is deliberately not part of the identity resolver in v1. If later
added, fuzzy results must be explicitly ranked suggestions, never silently
accepted as an exact concept identity.

### 3. Relation navigation is generic

The core read API should expose forms equivalent to:

```text
related(concept)
incoming(concept)
outgoing(concept)
```

These operate on canonical link relations.

A code adapter may later provide `callers`/`callees` when its projection policy
has created typed code relations. Those names must not leak into the generic
OKF core.

### 4. `diff` is relational, not textual

`diff` compares two compiled bundle states by stable concept identity and
normalized relational content.

The minimum result includes:

- concepts added / removed;
- concepts whose parsed/content digest changed;
- fields added / removed / changed where representable by the compiled type
  relations;
- links added / removed;
- diagnostics introduced / resolved.

A Markdown textual diff is useful complementary evidence but is not the
semantic diff contract of this command.

### 5. `impact` starts from the relational diff

`diff --impact` takes the changed concept identities/edges from `diff` and asks
which other concepts are reachable through canonical relation directions.

The implementation may use:

- DuckDB recursive CTEs;
- NetworkX traversal over the relation projection;
- another equivalent deterministic graph algorithm.

The choice is an implementation detail provided the edges come from the same
canonical relations and the output is deterministic.

The result should distinguish at least direct from transitive impact and record
path/depth information when requested. It must not imply causal impact merely
because a graph path exists; the contract is relational reachability.

### 6. `context` is generated, compact, and disposable

`context <concept>` produces an agent-oriented projection such as:

- identity/path/type;
- selected authored fields;
- diagnostic state;
- incoming/outgoing relations;
- optionally bounded neighbors to a requested depth.

It is generated from current relations on demand. A committed `SUMMARY.md` is
not required as duplicate state.

The default response must be bounded. Agents should be able to request deeper
context explicitly rather than receiving an unbounded graph neighborhood.

### 7. Persistent DuckDB is a first-class compiled image, not the authoring format

The existing persistent DuckDB export is important and should become more
useful to these features, but the distinction stays explicit:

```text
authored bundle          = canonical source
compiled DuckDB image    = query/interoperability snapshot
agent context / JSON     = transport projection
```

A future reusable compiled image may accelerate `query`, `lookup`, `diff`, and
`context`. Its correctness is defined entirely by whether it represents the
current authored bundle under the same compiler/configuration identity.

This RFC does not rename DuckDB files or introduce a new `.okf` binary file
format.

### 8. Incremental refresh reuses parser-owned digests

The benchmark's SHA256 dirty-update behavior is attractive, but `okf-parser`
already owns parsed/content digests and already has a native engine path for
large bundles.

Before introducing a new manifest format, implementation should benchmark the
relational materialization and use existing digests to identify changed
concepts. A persistent cache is justified only if measurements show meaningful
benefit after the `query`/lookup surfaces exist.

If introduced, incremental refresh updates a disposable compiled image; it does
not mutate authored OKF merely to keep the cache current.

### 9. Agent installation teaches the architecture instead of hiding it

A future command may install minimal integration instructions for Claude,
OpenCode, Codex, Cursor, or other agents.

The instructions should teach a small stable workflow, for example:

```text
before broad Markdown scanning → lookup/query/context
before changing a concept       → incoming/outgoing/related
before reviewing a knowledge change → diff --impact
after a change                  → check
```

The installer should not create a second knowledge bundle, generate domain
ontology, or require a long-lived MCP server. CLI usage is sufficient; MCP is an
optional transport over the same services.

### 10. MCP exposes high-level relational operations

The initial agent-facing MCP surface should prefer product operations such as:

```text
lookup
get_concept
related
incoming
outgoing
context
bundle_diff
impact
```

Names may be refined during implementation, but the semantic constraint is
fixed: each tool is a thin wrapper over the same read services used by the CLI.

Raw SQL is not required for the ordinary MCP path.

## Non-goals

This RFC does not:

- turn `okf-parser` into a source-code AST indexer;
- define code-language parsers;
- add embeddings or vector search;
- add an LLM question-answering layer;
- make DuckDB the authored OKF storage format;
- require persistent database materialization for every read;
- add a dashboard;
- define training-data export;
- accept fuzzy matches as canonical concept identity;
- replace NetworkX where graph algorithms are useful;
- change RFC 0005 mutation semantics;
- change RFC 0006 declaration trust semantics;
- change RFC 0008's preview/commit and effect-annotation model.

## Rejected architecture

### Direct `ConceptRecord` lookup as the new agent core

Rejected because it creates a shortcut around the relational compiler. It is a
reasonable low-level helper, but not the architectural foundation for the new
product surface.

### Graph-first agent API

Rejected because NetworkX is already a downstream projection. Making it the
agent authority would invert the documented architecture and make tabular/type
queries awkward.

### Copying `okf-generator`'s code ontology

Rejected because Function/Class/Module/caller/callee are properties of one
source domain, not universal OKF semantics.

### Committed summary/index documents as required state

Rejected because they duplicate facts already derivable from the bundle and
introduce stale-state problems. Generated summaries remain acceptable as an
explicit export if consumers need them.

### A new bespoke binary/index format

Rejected unless later measurement proves DuckDB/Ibis plus existing digests
cannot meet the required performance. The project already has a relational
engine and should exploit it before inventing another store.

## Implementation order after acceptance

Implementation should be a fresh stack based on the accepted RFC, not a repair
of the abandoned pre-RFC stack.

Recommended order:

1. **Query primitive** — resolve #151 on the shared relational materializer.
2. **Lookup and generic relation navigation** — CLI/service first, then MCP.
3. **Relational diff** — concept/digest/field/link/diagnostic changes.
4. **Impact analysis** — deterministic reachability over canonical relations.
5. **Bounded context projection** — compact agent-facing context.
6. **Agent installation helpers** — minimal CLI-first instructions, MCP optional.
7. **Incremental compiled-image optimization** — only after profiling the real
   workload and proving the cache is useful.

Each implementation PR must identify which layer it changes:

```text
authored parse
normalized relations
relational compilation
consumer service
transport adapter
```

A PR that cannot identify its layer or introduces the same semantic fact in two
layers should be treated as an architecture regression.

## Acceptance criteria

This RFC is ready to move from `proposed` to `accepted` when review agrees on:

1. authored OKF remains canonical and DuckDB remains compiled state;
2. query/lookup/diff/context are relational consumers, not independent indexes;
3. NetworkX stays a projection for graph algorithms;
4. generic core semantics remain domain-neutral;
5. the useful `okf-generator` product features to adopt/reject are explicit;
6. issue #151 is the first implementation primitive;
7. raw SQL versus structured MCP lookup has an honest trust/effect boundary;
8. incremental caching is derived/disposable and introduced only after
   measurement.

## References

- `okf-parser` architecture: `docs/architecture.md`
- RFC 0005: relational frontmatter writes
- RFC 0006: declared column types and DuckDB compilation
- RFC 0008: effect-aware MCP tools
- RFC 0011 proposal: tracked migrations
- issue #151: read-only relational query primitive
- `okf-generator`: <https://github.com/UmairBaig8/okf-generator>
