---
type: RFC
title: Agent-first bundle search and retrieval
status: proposed
description: Define one compact, provenance-preserving search surface over an OKF bundle, with offline lexical search by default and optional vector, hybrid and indexed retrieval profiles
---

# RFC 0009: Agent-first bundle search and retrieval

## Summary

`okf-parser` can validate, inspect, query and export a whole OKF bundle, but it
does not yet expose the most basic operation an agent needs once a bundle is
larger than its useful context window: **find the smallest useful passage that
answers this question**.

This RFC defines one public retrieval primitive:

```text
search(path, query, ...)
```

The public operation is stable while its retrieval profile may evolve. The
baseline is local lexical search over the parsed OKF bundle. DuckDB remains the
relational substrate and its `fts` extension is the preferred BM25
implementation when already available locally. Embeddings, vector indexes,
approximate nearest-neighbor acceleration, persistent sidecars and rerankers are
optional profile choices; they do not create new agent-facing tools.

The design has six invariants:

1. **Search the body and preserve its coordinates.** Every hit maps back to an
   exact contiguous range of one-based Markdown body lines.
2. **Body coordinates are evidence pointers, not stable identifiers.**
   `path.md#B35-B39` describes the current parsed body only.
3. **Compact output is the default contract.** The model should pay primarily
   for evidence, not repeated JSON keys and metadata.
4. **Offline lexical search is the zero-configuration behavior.** Search never
   requires an embedding provider, extension download or other network fetch
   merely to work.
5. **Indexes are derived state.** Markdown remains canonical; indexes and
   embeddings are rebuildable from the bundle plus an explicit retrieval
   profile.
6. **One tool, multiple retrieval profiles.** Agents do not choose between
   BM25, exact KNN, HNSW, Lance or another storage engine as separate tools.

The initial implementation is intentionally narrow: body-aware lexical
retrieval, compact rendering, deterministic ordering, filters and exact body
provenance. Vector and hybrid retrieval extend the same contract later.

## Motivation

### Reading the whole bundle is the wrong agent primitive

An OKF bundle may contain exactly the information an agent needs while still
being too large, repetitive or expensive to place wholesale in model context.
`inventory`, `graph`, generated schemas and DuckDB export help an agent
understand the bundle, but none answers a direct retrieval question such as:

```text
Where does this bundle discuss prescricao intercorrente?
```

Without a first-class search primitive, a client falls back to filesystem
search, custom SQL, ad-hoc embedding infrastructure, or sending substantially
more source text to the model than the task requires.

OKF already has unusually good retrieval inputs: parsed Markdown bodies,
producer-defined concept types, deterministic source digests, concept identity
and graph relationships. Search should expose those strengths directly.

### Retrieval cost is part of the product

The primary cost for an agent is not bytes on disk or tokenization of the whole
representation. It is the number of tokens that actually enter model context
before the task can be solved.

A verbose hit such as:

```json
{
  "concept_id": "tese/prescricao",
  "path": "teses/prescricao.md",
  "concept_type": "tese",
  "start_line": 35,
  "end_line": 36,
  "score": 8.314159,
  "text": "Prescricao intercorrente.\nO prazo volta a correr."
}
```

is useful for programs but unnecessarily expensive as the default model-facing
representation. The same evidence can usually be conveyed as:

```text
location\tsnippet
teses/prescricao.md#B35-B36\tPrescricao intercorrente. O prazo volta a correr.
```

Ranking order already communicates relative relevance. Rich metadata remains
available through `detail="full"` instead of being repeated in ordinary agent
context.

### The body already has a useful line representation

`okf-parser` already materializes a concept body as both `__okf_body` and
`__okf_body_lines VARCHAR[]` in the relational `apply` path. Search must reuse
the same body splitting semantics rather than inventing an independent notion
of a line.

Phase 1 should factor the existing `concept.body.splitlines()` behavior into
shared bundle/search support. Search may build additional passage relations for
ranking, but the body-line coordinate comes from that shared representation.

## Decision

### 1. Add one public `search` capability

The CLI and MCP expose the same conceptual operation:

```text
okf-parser search PATH QUERY
```

```text
search(path, query, ...)
```

The baseline public schema is:

```text
path: str
    Bundle root.

query: str
    Non-empty search text after trimming surrounding whitespace.

mode: "lexical" | "literal" | "vector" | "hybrid" = "lexical"
    Retrieval semantics. Exact versus approximate vector execution is not a
    public mode.

limit: int = 10
    Maximum returned passages. Must be >= 1.

context: int = 0
    Extra surrounding body lines requested by the caller. Must be >= 0.

exclude: list[str] = []
    Existing bundle exclusion patterns.

concept_type: str | None = None
    Optional producer-defined concept type filter.

path_glob: str | None = None
    Optional filter over bundle-relative paths already discovered in the bundle.

detail: "compact" | "score" | "full" = "compact"
    Agent-oriented compact rendering, compact rendering with score, or full
    structured result.

profile: str | None = None
    Optional named retrieval profile. Backend/index choices live here rather
    than in the agent-facing mode.
```

`concept_type` matches the bundle relation and avoids an ambiguous public
`type` field.

`mode="lexical"` remains the default even when embeddings are configured.
Installing or selecting an embedding profile must never silently change default
search semantics.

`mode="vector"` expresses semantic vector retrieval. Whether that query is
executed by exact DuckDB array distance, HNSW, Lance, or another compatible
index is a profile/backend decision. There is no public `mode="ann"`.

`path_glob` filters the paths already present in the loaded bundle; it does not
introduce an independent filesystem read mechanism.

### 2. Body-relative locations are canonical and ephemeral

Body line numbers are one-based. `B1` is the first line of the parsed Markdown
body regardless of frontmatter length.

Canonical compact locations are:

```text
relative/path.md#B37
relative/path.md#B35-B39
```

A one-line hit is serialized as `#B37`; a multi-line hit uses
`#B<start>-B<end>`.

**This identifier does not survive Markdown body edits; an agent must treat it
as ephemeral evidence for the current bundle state.**

Body-relative locations are therefore not concept identifiers, durable anchors
or references that should be persisted across edits. A `source_digest` in
`detail="full"` identifies the source version against which the location was
computed.

This RFC defines locations produced by `search`. It does not define a general
parser, normalizer or follow-up read API for arbitrary user-supplied `#B...`
ranges; that belongs to a future retrieval/read capability if needed.

### 3. Retrieval reuses body lines and derives passages

Search operates over the already parsed bundle rather than reparsing Markdown
independently.

The existing relational write path already carries:

```text
__okf_body
__okf_body_lines
```

Phase 1 promotes the body-line construction into reusable bundle/search
support. A logical body-line relation is:

```text
concept_id | path | body_line | text
```

Ranking may use a separate passage relation:

```text
search_passages
---------------
passage_id
concept_id
path
concept_type
title
body_start_line
body_end_line
text
source_digest
metadata
```

The passage relation exists for ranking quality; the line relation exists for
precise addressing and snippet construction. Paragraphs, Markdown blocks,
overlapping windows or sections may be ranked without losing exact body
coordinates.

Every returned hit must map to a contiguous range in the shared body-line
representation.

### 4. Chunking is structural and replaceable

The passage builder should prefer Markdown structure before applying a size
bound. Candidate strategies include:

- `line` -- one body line per passage;
- `paragraph` -- Markdown paragraph/block boundaries;
- `section` -- heading-delimited sections;
- `window` -- bounded overlapping line windows;
- `document` -- one concept body per passage.

The initial implementation may use a simpler line/window strategy, provided it
preserves body coordinates and does not freeze that choice into the public
`search` API.

Chunking parameters belong to a retrieval profile and become part of its
fingerprint.

### 5. Compact output is normative and faithful to whole body lines

The default model-facing rendering is a compact two-column TSV-like form:

```text
location\tsnippet
teses/prescricao.md#B35-B36\tPrescricao intercorrente. O prazo volta a correr.
recursos/apelacao.md#B81\tO recurso sustenta o reconhecimento da prescricao.
```

Each hit occupies exactly one physical output line in compact mode.

The compact rendering contract is:

- location and snippet are separated by one tab;
- the snippet contains the complete text of every body line named by the
  returned range, in order;
- line breaks between those body lines are rendered as one ASCII space;
- tabs inside body text are rendered as spaces;
- rendering whitespace may be normalized for the single-line representation;
- Phase 1 never cuts inside a selected body line;
- the renderer never omits a suffix or prefix of a selected line while keeping
  a location that claims the whole line;
- `detail="full"` exposes the original passage text and body range without
  depending on this single-line normalization.

If a ranked passage is too large for the desired compact retrieval behavior,
the ranking/chunking layer must choose a smaller **contiguous set of whole body
lines before rendering**. It must not truncate text inside a line and then
reuse the old location.

Likewise, the renderer does not expand a short hit merely to fill an arbitrary
budget. Explicit `context=N` may add neighboring whole body lines; the returned
location expands to exactly those lines.

There is no `max_snippet_tokens` contract in Phase 1. Token-aware clipping can
be added only after the project chooses a tokenizer/counting contract and a
representation that preserves truthful provenance.

`detail="score"` adds one compact score column. `detail="full"` returns a
structured object suitable for programs and debugging.

Canonical compact example:

```text
location\tsnippet
teses/prescricao.md#B35-B36\tPrescricao intercorrente. O prazo volta a correr.
```

Canonical full-detail example:

```json
{
  "query": "prescricao intercorrente",
  "mode": "lexical",
  "engine": "duckdb_fts",
  "results": [
    {
      "rank": 1,
      "score": 8.31,
      "concept_id": "teses/prescricao",
      "concept_type": "tese",
      "path": "teses/prescricao.md",
      "location": "teses/prescricao.md#B35-B36",
      "body_start_line": 35,
      "body_end_line": 36,
      "source_digest": "sha256:...",
      "text": "Prescricao intercorrente.\nO prazo volta a correr."
    }
  ]
}
```

The exact full-detail serialization may add diagnostics/profile metadata, but
the compact contract is deliberately small and stable.

### 6. Lexical search is offline by contract; DuckDB FTS is preferred when local

DuckDB's `fts` extension provides full-text indexes and BM25 ranking and is the
preferred lexical implementation when the extension is already installed and
loadable locally.

Official documentation:

- <https://duckdb.org/docs/current/core_extensions/full_text_search>
- <https://duckdb.org/docs/current/extensions/overview>

DuckDB can autoinstall/autoload known extensions, which may require network
access on first use. Therefore default `okf-parser search` must not rely on that
path.

For the default local/closed-world search path:

1. use `fts` only when it is already available locally;
2. do not issue `INSTALL fts` as an implicit side effect of a search call;
3. do not trigger an extension autoinstall/download path merely by searching;
4. do not require network access to make `mode="lexical"` work;
5. if `fts` is unavailable, use a deterministic built-in lexical fallback over
   the derived body/passages relation;
6. expose the resolved engine in `detail="full"` for diagnostics and benchmark
   reproducibility.

The fallback is a ranked lexical search, not an implicit downgrade to
`mode="literal"`. Its exact scoring formula is an implementation detail to be
frozen by tests for Phase 1. A deployment/profile may require FTS explicitly
and fail clearly when unavailable, but that is not the default.

The FTS index does not automatically track changes to its input table. Search
therefore owns refresh/invalidation and must never present a stale index as
current bundle state.

### 7. Literal mode is a small explicit fallback semantic

`mode="literal"` means case-insensitive literal substring matching, not regular
expressions. It remains useful for exact phrase fragments and debugging and
still returns body-line provenance using the same compact contract.

Regex search may be added later under an explicit semantic rather than changing
`literal` behavior.

### 8. Ranking and tie-breaking are deterministic

Every engine normalizes its result ordering into a rank where the best result is
first. When an exposed numeric score is used, a larger normalized `score` means
better relevance.

For equal normalized scores, the canonical tie-break is:

```text
(score DESC, path ASC, body_start_line ASC)
```

If two passages still tie, `body_end_line ASC` and then `passage_id ASC` finish
the ordering.

For a fixed bundle, resolved retrieval profile and engine availability, result
order must be deterministic.

### 9. Vector search does not expose ANN as user intent

A configured embedding profile may materialize fixed-size vectors alongside
passages. DuckDB fixed-size arrays support native distance/similarity functions,
so `mode="vector"` can begin with exact top-k scanning.

Exact versus approximate execution is deliberately hidden behind the retrieval
profile:

```text
mode="vector", profile="legal-local-exact"
mode="vector", profile="legal-local-hnsw"
mode="vector", profile="legal-lance"
```

All three have the same agent-facing semantics: retrieve by vector similarity
and return provenance-preserving passages.

DuckDB VSS/HNSW, Lance or another ANN implementation is an acceleration/backend
choice. Parameters such as `ef_search`, `M` and `ef_construction` belong to the
profile, not the public `mode` enum.

### 10. Embedding generation is a provider protocol

Embeddings are derived state, not OKF canonical data. The provider contract
distinguishes document and query embeddings:

```text
embed_documents(texts) -> vectors
embed_query(query) -> vector
```

A profile records enough metadata to reject incompatible or stale vectors:

```text
provider/model identifier
provider/model revision or fingerprint when available
dimensions
distance metric
normalization contract
chunker + chunker version
backend/index parameters
```

Providers may be local functions, local models, subprocesses, HTTP services or
hosted APIs. The core search API depends on this contract, not on a particular
vendor SDK.

External providers have privacy, cost and network effects. Those effects are
explicit profile properties and must be reflected in MCP annotations under RFC
0008.

### 11. Hybrid retrieval is a ranking policy behind the same tool

`mode="hybrid"` combines lexical and vector candidates behind the same `search`
contract.

Reciprocal Rank Fusion is a baseline candidate because it combines ranks without
pretending BM25 scores and vector distances share a common numeric scale.
Weighted fusion or staged reranking may be profile choices.

The exact RRF `k`, weights and candidate pool sizes remain benchmark questions,
not Phase 1 public API.

### 12. Persistent retrieval stores are optional derived backends

Large bundles may benefit from a persistent sidecar. That sidecar must preserve
the same passage identity and body-line provenance as the in-process
representation.

Lance is a candidate because the DuckDB Lance extension supports vector, FTS and
hybrid operations. SQLite-based or other vector adapters may also be supported
if they satisfy the same retrieval contract.

The architecture remains:

```text
canonical Markdown bundle
        |
        +-- shared parsed/body-line representation
        +-- ephemeral DuckDB relations
        +-- optional local FTS/vector indexes
        +-- optional persistent retrieval sidecar
```

Backend choice is a profile concern, not an OKF semantic.

### 13. Structured OKF filters apply before or during retrieval

Search should exploit the fact that OKF knows concepts and structured metadata.
Filters may narrow candidates by:

- `concept_type`;
- bundle-relative path/path glob;
- concept identity;
- declared frontmatter fields where safely expressible;
- later, graph neighborhood or link predicates.

Whenever possible, filters are pushed down before expensive vector ranking.

### 14. Result expansion preserves whole-line provenance

A hit identifies a useful ranked passage. The caller may request surrounding
body context:

```text
context=2
```

Expansion adds neighboring whole body lines, is bounded by the body and updates
the returned location to exactly the lines returned. It never silently returns
the full body.

A future exact output-token budget may be added only after the project chooses a
tokenizer/counting contract and specifies how token-driven selection maps back
to truthful body-line provenance.

### 15. Indexes and embeddings are digest-addressed derived state

The project already computes deterministic source/parsed digests. Retrieval
state should use source digests plus a retrieval-profile fingerprint for
incremental refresh:

```text
same source digest + same profile -> reuse derived rows/vectors
changed source digest             -> rebuild affected passages
removed concept                   -> remove derived passages
new concept                       -> add derived passages
```

The profile fingerprint includes every choice that changes passage identity or
vector meaning.

No Markdown file is rewritten merely to store an embedding.

### 16. MCP effect metadata follows the resolved profile

The default offline lexical search is local inspection and can be exposed as
read-only, idempotent and closed-world.

Search must not silently install DuckDB extensions or write a persistent
sidecar as part of that call.

A configured hosted embedding provider can perform network I/O, so its MCP
`search` tool must conservatively advertise `openWorldHint=true` under RFC 0008.
Persistent index build/update operations, if exposed, receive their own explicit
effect contract.

### 17. Runtime ownership and cross-runtime parity are explicit

The search contract is runtime-neutral. Location syntax, compact rendering,
literal semantics, ordering, errors and conformance fixtures belong to the
shared observable contract established by RFC 0002.

Phase 1 implementation ownership is:

- **Python:** the first implementation lives in the existing Python service
  layer, preferably a focused search module called by `service.py`, with CLI and
  MCP adapters. DuckDB-backed lexical ranking belongs here.
- **Rust core:** Rust continues to provide parsing/bundle acceleration where
  already used. Phase 1 does not add a second Rust search engine.
- **TypeScript core:** no independent search semantics are invented; shared
  location/output fixtures are added to `conformance/` with the Python work.
- **`okf-parser-duckdb` (`typescript-duckdb`):** DuckDB-specific TypeScript
  search parity belongs in this adapter when implemented, reusing the same
  conformance cases.

Python delivery may land before the TypeScript adapter implementation,
consistent with RFC 0002's incremental parity model. No release should claim
cross-runtime search parity until both implementations pass the shared search
conformance cases.

### 18. Canonical examples and errors

Minimal call:

```text
search(
  path=".",
  query="prescricao intercorrente"
)
```

Compact result:

```text
location\tsnippet
teses/prescricao.md#B35-B36\tPrescricao intercorrente. O prazo volta a correr.
recursos/apelacao.md#B81\tO recurso sustenta o reconhecimento da prescricao.
```

Full detail:

```text
search(
  path=".",
  query="prescricao intercorrente",
  concept_type="tese",
  detail="full"
)
```

```json
{
  "query": "prescricao intercorrente",
  "mode": "lexical",
  "engine": "duckdb_fts",
  "results": [
    {
      "rank": 1,
      "score": 8.31,
      "concept_id": "teses/prescricao",
      "concept_type": "tese",
      "path": "teses/prescricao.md",
      "location": "teses/prescricao.md#B35-B36",
      "body_start_line": 35,
      "body_end_line": 36,
      "source_digest": "sha256:...",
      "text": "Prescricao intercorrente.\nO prazo volta a correr."
    }
  ]
}
```

Canonical errors remain limited to inputs that `search` itself consumes. For
example:

```text
search(path=".", query="   ")
-> error: query must not be empty
```

```text
search(path=".", query="prazo", limit=0)
-> error: limit must be at least 1
```

A future tool that consumes `#B...` ranges defines its own range-validation
contract rather than borrowing one implicitly from this RFC.

### 19. Search is a benchmarkable retrieval strategy

The benchmark suite treats compact OKF search as a first-class strategy. For a
fixed task and fixed source information, the **primary metric is the number of
tokens actually placed in the agent context until the task is resolved**.

That includes:

- search tool-call arguments;
- compact search results;
- follow-up passage/concept retrieval;
- additional tool responses needed to answer;
- final evidence context consumed by the model.

Candidate comparisons include:

```text
whole Markdown corpus
whole OKF bundle
filesystem grep
OKF lexical compact
OKF vector compact
OKF hybrid compact
```

Secondary diagnostic metrics include:

- task success/answer correctness;
- recall@k;
- MRR or equivalent rank quality;
- latency;
- index/build cost;
- embedding/network cost where applicable.

Those diagnostics explain why a strategy spends more or fewer context tokens.
They do not replace actual agent-context consumption as the optimization target.

## Proposed implementation sequence

### Phase 1: body-aware lexical search

1. factor/reuse the existing body-line splitting semantics;
2. add body/passages relations with one-based coordinates;
3. implement offline lexical ranking;
4. use local DuckDB FTS/BM25 when available without implicit installation or
   extension download;
5. add case-insensitive literal substring mode;
6. implement `concept_type` and bundle-relative path filters;
7. implement compact whole-line snippet rendering;
8. implement deterministic tie-breaking;
9. expose one `search` capability through Python service, CLI and MCP;
10. add shared conformance fixtures for locations and compact output;
11. benchmark actual agent-consumed search-result tokens.

### Phase 2: exact vectors

1. define the embedding provider/profile protocol;
2. materialize passage vectors as fixed-size arrays;
3. use native DuckDB distance functions for exact top-k;
4. use source/profile digests for incremental reuse;
5. keep vector search opt-in.

### Phase 3: hybrid retrieval

1. retrieve lexical and vector candidate sets;
2. implement deterministic RRF or another measured fusion policy;
3. push structured filters before expensive ranking;
4. measure quality and agent-token cost against lexical-only retrieval.

### Phase 4: indexed acceleration and persistent sidecars

1. add optional HNSW/VSS as a vector profile/backend optimization;
2. benchmark recall/latency against exact vector search;
3. evaluate Lance and compatible sidecars for large bundles;
4. keep every index rebuildable and provenance-preserving.

### Phase 5: TypeScript search parity

1. implement the shared search contract in the appropriate TypeScript surface;
2. put DuckDB-specific search in `typescript-duckdb`;
3. run the same conformance fixtures;
4. document when cross-runtime search parity is achieved.

### Phase 6: advanced policies

Only after benchmark evidence justifies them:

- reranker providers;
- tokenizer-aware result packing;
- graph-neighborhood expansion;
- diversity/MMR-style selection;
- multiple named embedding profiles;
- query routing among literal, lexical, vector and hybrid modes.

None requires a new agent-facing search tool.

## Non-goals

This RFC does not:

- make embeddings part of the canonical OKF format;
- require network access for default bundle search;
- require a hosted embedding vendor;
- require VSS/HNSW;
- expose ANN/HNSW as a public retrieval semantic;
- turn `okf-parser` into a general-purpose vector database;
- promise semantic/vector search in Phase 1;
- return full document bodies by default;
- define one permanent chunking algorithm;
- make a persistent sidecar authoritative over Markdown;
- make body-line locations stable across edits;
- define arbitrary `#B...` range parsing in Phase 1;
- silently install extensions or execute hosted embedding calls;
- require Rust to own search ranking.

## Alternatives considered

### Separate tools for each algorithm

`search_bm25`, `search_vector`, `search_hnsw` and `search_hybrid` leak backend
details into the agent's tool-selection problem and increase schema/context
overhead. Rejected in favor of one `search` tool.

### Public `ann` mode

`ann` describes an execution strategy, not user retrieval intent. It also leaks
HNSW-like concerns into the stable API. Rejected; exact versus approximate
execution belongs to the vector profile.

### Network-dependent FTS as the only lexical path

DuckDB can autoinstall `fts`, which may fetch an extension on first use. Making
that the only implementation would contradict offline zero-configuration
search. Rejected. Local FTS is preferred, with an offline lexical fallback.

### Intra-line snippet truncation

Cutting a line around a high-scoring span while returning a location for the
whole line makes the provenance claim false. Rejected for Phase 1. Compact
snippets render whole body lines; smaller results come from selecting a smaller
contiguous line range before rendering.

### Durable body-line anchors

Hashes or heading anchors could make evidence locations survive some edits, but
they complicate Phase 1 and do not improve the immediate retrieval problem.
Rejected for now. Body-line locations are intentionally ephemeral.

### Dedicated vector database as a mandatory dependency

This adds deployment complexity before exact DuckDB vector search has shown
itself insufficient. Rejected; specialized stores remain optional backends.

## Open questions

These should be answered empirically rather than frozen prematurely:

1. Which structural passage strategy gives the best retrieval quality per
   returned token?
2. Should lexical Phase 1 rank passages, individual body lines, or a two-stage
   combination?
3. What deterministic fallback lexical scorer best approximates useful BM25
   behavior when local `fts` is unavailable?
4. At what corpus size does exact vector scan become materially worse than an
   ANN profile?
5. Does RRF materially outperform staged lexical/vector reranking?
6. Where should named retrieval profiles live?
7. Which tokenizer contract, if any, should govern a future exact output-token
   budget without weakening body-line provenance?
8. When should Rust take on retrieval preprocessing, if profiling demonstrates
   Python-side passage construction is material?

These questions do not block Phase 1.

## Acceptance criteria

The baseline implementation is complete when it demonstrates:

1. `okf-parser search PATH QUERY` and MCP `search(path, query)` over a bundle;
2. public modes limited to `lexical`, `literal`, `vector` and `hybrid`;
3. default lexical retrieval with no embedding provider and no required network
   access;
4. local DuckDB FTS/BM25 use when available, with no implicit `INSTALL` or
   extension download and a deterministic offline lexical fallback otherwise;
5. each hit mapped to an exact contiguous one-based body line range;
6. explicit warning/contract that body locations are ephemeral across edits;
7. compact one-physical-line-per-hit `location + snippet` output;
8. compact snippets containing the complete text of the body lines named by
   their location, with no intra-line truncation;
9. explicit `detail="full"` structured output;
10. deterministic ordering by normalized score, path and body start line;
11. non-empty-query and basic argument validation;
12. reuse of the existing body-line semantics instead of an independent split;
13. no canonical Markdown mutation, implicit extension installation or network
    fetch as a side effect of default search;
14. shared conformance fixtures covering location rendering, compact output,
    exclusions and filters;
15. tests for multilingual/accented body text;
16. a benchmark recording actual agent-consumed tokens as the primary cost
    metric, with recall/rank quality/latency as secondary diagnostics.

Vector, hybrid, ANN acceleration and persistent sidecars are accepted extensions
of this architecture, not blockers for Phase 1.
