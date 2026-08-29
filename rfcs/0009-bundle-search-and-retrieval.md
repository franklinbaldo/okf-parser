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
   requires an embedding provider or a network fetch merely to work.
5. **Indexes are derived state.** Markdown remains canonical; indexes and
   embeddings are rebuildable from the bundle plus an explicit profile.
6. **One tool, multiple retrieval profiles.** Agents do not choose between
   BM25, exact KNN, HNSW, Lance or another storage engine as separate tools.

The initial implementation is intentionally narrow: body-aware lexical
retrieval, compact rendering, deterministic ordering, safe bundle-relative
locations and filters. Vector and hybrid retrieval extend the same contract
later.

## Motivation

### Reading the whole bundle is the wrong agent primitive

An OKF bundle may contain exactly the information an agent needs while still
being too large, too repetitive or too expensive to place wholesale in model
context. `inventory`, `graph`, generated schemas and DuckDB export help an agent
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

The primary cost for an agent is not bytes on disk or the tokenization of the
whole representation. It is the number of tokens that actually enter model
context before the task can be solved.

A verbose hit such as:

```json
{
  "concept_id": "tese/prescricao",
  "path": "teses/prescricao.md",
  "concept_type": "tese",
  "start_line": 35,
  "end_line": 39,
  "score": 8.314159,
  "text": "..."
}
```

is useful for programs but unnecessarily expensive as the default
model-facing representation. The same evidence can usually be conveyed as:

```text
location\tsnippet
teses/prescricao.md#B35-B39\tA prescricao intercorrente ocorre quando ...
recursos/apelacao.md#B81-B84\t... reconhecimento da prescricao intercorrente ...
```

Ranking order already communicates relative relevance. Rich metadata remains
available through `detail="full"` instead of being repeated in ordinary agent
context.

### The body already has a useful line representation

`okf-parser` already materializes a concept body as both `__okf_body` and
`__okf_body_lines VARCHAR[]` in the relational `apply` path. Search must reuse
the same body splitting semantics rather than inventing an independent notion
of a line.

Phase 1 should factor the existing `concept.body.splitlines()` behavior into a
shared helper/relation that both relational writes and retrieval can consume.
Search may build additional passage relations for ranking, but the canonical
body-line coordinate comes from that shared representation.

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
    Retrieval semantics. Exact vs approximate vector execution is deliberately
    not a public mode.

limit: int = 10
    Maximum returned passages. Must be >= 1.

context: int = 0
    Extra surrounding body lines requested by the caller. Must be >= 0.

exclude: list[str] = []
    Existing bundle exclusion patterns.

concept_type: str | None = None
    Optional producer-defined concept type filter.

path_glob: str | None = None
    Optional bundle-relative path filter.

detail: "compact" | "score" | "full" = "compact"
    Agent-oriented compact rendering, compact rendering with score, or full
    structured result.

profile: str | None = None
    Optional named retrieval profile. Backend/index choices live here rather
    than in the agent-facing mode.
```

`concept_type` is used instead of `type` to match the bundle relation and avoid
an ambiguous public schema name.

`mode="lexical"` remains the default even when embeddings are configured.
Installing or selecting an embedding profile must never silently change default
search semantics.

`mode="vector"` expresses semantic vector retrieval. Whether that query is
executed by exact DuckDB array distance, HNSW, Lance, or another compatible
index is a profile/backend decision. There is no public `mode="ann"`.

### 2. Paths and locations are bundle-confined

`path` identifies the bundle root using the same root-resolution rules as the
other `okf-parser` services. Every path *inside* the retrieval contract is then
bundle-relative.

In particular:

- returned `location` paths are relative to the resolved bundle root;
- `path_glob` is interpreted only inside that root;
- absolute document selectors are rejected;
- any selector containing a traversal that can resolve outside the bundle
  (including `..`) is rejected;
- symlink/resolution handling must not permit an apparent bundle-relative
  selector to escape the resolved root.

A search service must never read an arbitrary path outside the selected bundle
because a query or filter encoded one.

Canonical path-related errors include:

```text
path escapes bundle root
path is outside bundle root
```

Deployments may separately restrict which bundle roots an MCP server is allowed
to expose. That authorization boundary is outside this RFC.

### 3. Body-relative location syntax is canonical and ephemeral

Body line numbers are one-based. `B1` is the first line of the parsed Markdown
body regardless of frontmatter length.

Canonical compact locations are:

```text
relative/path.md#B37
relative/path.md#B35-B39
```

A one-line hit is always serialized as `#B37`, never `#B37-B37`.

**This identifier does not survive Markdown body edits; an agent must treat it
as ephemeral evidence for the current bundle state.**

Body-relative locations are therefore not concept identifiers, durable anchors,
or references that should be persisted across edits. A `source_digest` in
`detail="full"` identifies the source version against which the location was
computed.

Adapters may additionally expose physical source-file lines for editor
navigation, but those are not the compact retrieval coordinate.

#### Range normalization and validation

Any component that parses a body range follows one rule set:

- `B0` and negative values are invalid;
- an inverted range such as `B39-B35` is normalized to `B35-B39`;
- a range whose end exceeds the body is clamped to the last existing body line;
- a range whose entire normalized interval is outside the body fails with
  `interval out of body bounds`;
- an empty body has no valid `B` location.

Examples:

```text
# body has 40 lines
B39-B35  -> B35-B39
B38-B99  -> B38-B40
B41-B50  -> error: interval out of body bounds
B0       -> error: invalid body line
```

Search itself normally produces valid ranges rather than accepting them as a
query argument, but the location parser/renderer is part of the shared service
contract so follow-up retrieval and adapters do not invent incompatible rules.

### 4. Retrieval operates over shared body lines plus derived passages

Search operates over the already parsed bundle rather than reparsing Markdown
independently.

The existing relational write path already carries:

```text
__okf_body
__okf_body_lines
```

Phase 1 promotes the body-line construction into reusable bundle/search support.
A logical body-line relation is:

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

The implementation must not manufacture a hit that cannot be mapped back to a
contiguous range in the shared body-line representation.

### 5. Chunking is structural and replaceable

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

### 6. Compact output is normative and single-line per hit

The default model-facing rendering is a compact two-column TSV-like form:

```text
location\tsnippet
teses/prescricao.md#B35-B39\tA prescricao intercorrente ocorre quando ...
recursos/apelacao.md#B81-B84\t... reconhecimento da prescricao intercorrente ...
```

Each hit occupies exactly one physical output line in compact mode.

To make that contract unambiguous:

- the location and snippet are separated by one tab;
- line breaks inside the selected body range are rendered as one ASCII space;
- tabs inside body text are rendered as spaces;
- runs of rendering whitespace may be collapsed to one space;
- the underlying body text is not modified; this normalization is only the
  compact renderer;
- `detail="full"` exposes the original passage text and body range without
  depending on this single-line rendering.

This representation is intentionally optimized for model context rather than
round-tripping source Markdown.

`detail="score"` adds one compact score column. `detail="full"` returns a
structured object suitable for programs and debugging.

Canonical compact example:

```text
location\tsnippet
teses/prescricao.md#B35-B39\tPrescricao intercorrente. O prazo volta a correr ...
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
      "location": "teses/prescricao.md#B35-B39",
      "body_start_line": 35,
      "body_end_line": 39,
      "source_digest": "sha256:...",
      "text": "Prescricao intercorrente.\nO prazo volta a correr ..."
    }
  ]
}
```

The exact full-detail serialization may add diagnostics/profile metadata, but
the compact contract is deliberately small and stable.

### 7. Snippet construction has a deterministic truncation policy

Search ranking and snippet rendering are separate concerns. The engine first
identifies a ranked body range; the renderer then constructs a bounded snippet.

The Phase 1 truncation policy is:

1. anchor truncation around the highest-relevance matched span within the ranked
   passage;
2. when truncation is necessary, prefer a window centered on that span rather
   than always taking the passage prefix;
3. never cut in the middle of a word;
4. prefer a sentence boundary when one is available without materially
   exceeding the rendering budget;
5. include at most two preceding lines of structural context (for example a
   heading or immediately preceding paragraph context), and only when those
   lines are already part of requested/selected context;
6. if the ranked passage is already below the snippet ceiling, do not expand it
   merely to fill the ceiling;
7. explicit `context=N` may expand the selected body range, after which the same
   truncation rules apply;
8. any truncation keeps the returned `location` equal to the body lines from
   which the rendered snippet was actually drawn.

A target of roughly **96-128 model tokens per hit** is the initial benchmark
range. Phase 1 may implement that as a deterministic tokenizer-independent
rendering bound; an exact `max_snippet_tokens` parameter is not normative until
the project adopts an explicit tokenizer contract.

The benchmark, not intuition, should determine the eventual default.

### 8. Lexical search is offline by contract; DuckDB FTS is preferred when local

DuckDB's `fts` extension provides full-text indexes and BM25 ranking and is the
preferred lexical implementation when the extension is already installed and
loadable locally.

Official documentation:

- <https://duckdb.org/docs/current/core_extensions/full_text_search>
- <https://duckdb.org/docs/current/extensions/overview>

DuckDB can transparently autoinstall/autoload known extensions. That may require
network access on first use. Therefore `okf-parser search` must **not** rely on
autoload installation to satisfy its zero-configuration contract.

For the default local/closed-world search path:

1. use `fts` when it is already available locally;
2. do not issue `INSTALL fts` as an implicit side effect of a search call;
3. do not require network access to make `mode="lexical"` work;
4. if `fts` is unavailable, use a deterministic built-in lexical fallback over
   the derived body/passages relation;
5. expose the resolved engine in `detail="full"` for diagnostics and benchmark
   reproducibility.

The fallback is a ranked lexical search, not an implicit downgrade to
`mode="literal"`. Its exact scoring formula is an implementation detail to be
frozen by tests for Phase 1. A deployment/profile may require `fts` explicitly
and fail clearly if it is unavailable, but that is not the default.

The FTS index does not automatically track changes to its input table. Search
therefore owns refresh/invalidation and must never present a stale index as
current bundle state.

### 9. Literal mode means case-insensitive substring search, not regex

`mode="literal"` is the smallest no-index retrieval semantic:

- query is a literal substring, not a regular expression;
- matching is case-insensitive using the runtime's documented Unicode
  case-normalization behavior;
- punctuation and whitespace in the query are ordinary literal content;
- results still preserve body-line provenance and deterministic tie-breaking.

Regex search may be added later as an explicit mode/flag. It must not be
smuggled into `literal`, because regex syntax changes both semantics and error
behavior.

### 10. Ranking and tie-breaking are deterministic

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

### 11. Vector search does not require ANN

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

### 12. Embedding generation is a provider protocol

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

### 13. ANN is an acceleration profile, not a mode

DuckDB's `vss` extension can provide HNSW acceleration over fixed-size vectors.
HNSW parameters such as `ef_search`, `M` and `ef_construction` belong to the
retrieval profile.

The public API does not expose `ann` or `hnsw` as retrieval semantics. An agent
asking for vector relevance should not need to know whether the backend scans
exactly or uses an approximate index.

ANN indexes remain rebuildable acceleration structures. They are never
canonical OKF state.

### 14. Persistent vector stores are optional backends

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

### 15. Hybrid retrieval is a ranking policy, not a second tool

`mode="hybrid"` combines lexical and vector candidates behind the same `search`
contract.

Reciprocal Rank Fusion is the baseline candidate because it combines ranks
without pretending BM25 scores and vector distances share a common numeric
scale. Weighted fusion or staged reranking may be profile choices.

The exact RRF `k`, weights and candidate pool sizes remain benchmark questions,
not Phase 1 public API.

### 16. Structured OKF filters apply before or during retrieval

Search should exploit the fact that OKF knows concepts and structured metadata.
Filters may narrow candidates by:

- `concept_type`;
- bundle-relative path/path glob;
- concept identity;
- declared frontmatter fields where safely expressible;
- later, graph neighborhood or link predicates.

Whenever possible, filters are pushed down before expensive vector ranking.

### 17. Result expansion is bounded and explicit

A hit identifies a useful ranked passage. The caller may request surrounding
body context:

```text
context=2
```

Expansion is clamped to the body and updates the returned location. It never
silently returns the full body.

A future exact output-token budget may be added after the project chooses a
tokenizer/counting contract. Until then, compact rendering is bounded by the
deterministic snippet policy above.

### 18. Indexes and embeddings are digest-addressed derived state

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

### 19. MCP effect metadata follows the resolved profile

The default offline lexical search is local inspection and can be exposed as
read-only, idempotent and closed-world.

Search must not silently install DuckDB extensions or write a persistent
sidecar as part of that call.

A configured hosted embedding provider can perform network I/O, so its MCP
`search` tool must conservatively advertise `openWorldHint=true` under RFC 0008.
Persistent index build/update operations, if exposed, receive their own explicit
effect contract.

### 20. Runtime ownership and cross-runtime parity are explicit

The search *contract* is runtime-neutral. Location syntax, validation, compact
rendering, literal semantics, ordering, error behavior and conformance fixtures
belong to the shared observable contract established by RFC 0002.

Phase 1 implementation ownership is:

- **Python:** the first implementation lives in the existing Python service
  layer (preferably a focused search module called by `service.py`) and exposes
  CLI + MCP adapters. DuckDB-backed lexical ranking belongs here.
- **Rust core:** Rust continues to provide parsing/bundle acceleration where
  already used. Phase 1 does not add a second Rust search engine. Shared
  body-line extraction may later move downward only if it preserves the exact
  conformance contract.
- **TypeScript core:** no independent search semantics are invented. Shared
  location/snippet/error fixtures are added to `conformance/` with the Python
  implementation.
- **`okf-parser-duckdb` (`typescript-duckdb`):** DuckDB-specific TypeScript
  search parity belongs in this adapter when implemented, reusing the same
  conformance cases and compact observable contract.

Python delivery is allowed to land before the TypeScript adapter implementation,
consistent with RFC 0002's incremental parity model. However, the capability
must be recorded in the shared conformance surface immediately, and TypeScript
parity is a named follow-up rather than an unspecified future task.

No release should claim cross-runtime search parity until both implementations
pass the shared search conformance cases.

### 21. Canonical examples and errors

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
teses/prescricao.md#B35-B39\tPrescricao intercorrente. O prazo volta a correr ...
recursos/apelacao.md#B81-B84\tO recurso sustenta o reconhecimento da prescricao ...
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
      "location": "teses/prescricao.md#B35-B39",
      "body_start_line": 35,
      "body_end_line": 39,
      "source_digest": "sha256:...",
      "text": "Prescricao intercorrente.\nO prazo volta a correr ..."
    }
  ]
}
```

Canonical errors:

```text
search(path=".", query="   ")
-> error: query must not be empty
```

```text
search(path=".", query="prazo", path_glob="../../secret.md")
-> error: path escapes bundle root
```

Location parser examples:

```text
docs/a.md#B0
-> error: invalid body line
```

```text
docs/a.md#B500-B600
-> error: interval out of body bounds
```

Errors should be structured internally even when the CLI renders concise prose.

### 22. Search is a benchmarkable retrieval strategy

The benchmark suite treats compact OKF search as a first-class strategy. For a
fixed task and fixed source information, the **primary metric** is the number of
tokens actually placed in the agent context until the task is resolved,
including:

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

Those diagnostics explain *why* a strategy spends more or fewer context tokens.
They do not replace actual agent-context consumption as the optimization target.

## Proposed implementation sequence

### Phase 1: body-aware lexical search

1. factor/reuse the existing body-line splitting semantics;
2. add body/passages relations with one-based coordinates;
3. implement offline lexical ranking;
4. use local DuckDB FTS/BM25 when available without implicit installation;
5. add case-insensitive literal substring mode;
6. implement `concept_type` and safe bundle-relative path filters;
7. implement canonical location parsing/range validation;
8. implement deterministic snippet rendering and truncation;
9. implement deterministic tie-breaking;
10. expose one `search` capability through Python service, CLI and MCP;
11. add shared conformance fixtures for locations, errors and compact output;
12. benchmark actual agent-consumed search-result tokens.

### Phase 2: exact vectors

1. define embedding provider/profile protocol;
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
- silently install extensions or execute hosted embedding calls;
- require Rust to own search ranking.

## Alternatives considered

### Separate MCP tools for each algorithm

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

### JSON-only output

JSON is useful for programs but expensive for the dominant agent-reading case.
Rejected as the default; `detail="full"` keeps structured data available.

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
4. What exact snippet ceiling within the 96-128-token target minimizes follow-up
   calls?
5. At what corpus size does exact vector scan become materially worse than an
   ANN profile?
6. Does RRF materially outperform staged lexical/vector reranking?
7. Where should named retrieval profiles live?
8. Which tokenizer contract, if any, should govern a future exact
   `max_snippet_tokens`/`max_tokens` API?
9. When should Rust take on retrieval preprocessing, if profiling demonstrates
   Python-side passage construction is material?

These questions do not block Phase 1.

## Acceptance criteria

The baseline implementation is complete when it demonstrates:

1. `okf-parser search PATH QUERY` and MCP `search(path, query)` over a bundle;
2. default lexical retrieval with no embedding provider and no required network
   access;
3. local DuckDB FTS/BM25 use when available, with deterministic offline lexical
   fallback;
4. each hit mapped to an exact contiguous one-based body line range;
5. canonical single-line and range locations, including range validation rules;
6. explicit warning/contract that body locations are ephemeral across edits;
7. compact one-physical-line-per-hit `location + snippet` output;
8. deterministic snippet truncation behavior;
9. explicit `detail="full"` structured output;
10. deterministic ordering by normalized score, path and body start line;
11. safe bundle confinement for path filters/selectors;
12. non-empty-query validation and stable errors;
13. reuse of the existing body-line semantics instead of an independent split;
14. no canonical Markdown mutation or implicit extension installation as a side
    effect of default search;
15. shared conformance fixtures covering location rendering, compact output,
    errors, exclusions and filters;
16. tests for multilingual/accented body text;
17. a benchmark recording actual agent-consumed tokens as the primary cost
    metric, with recall/rank quality/latency as secondary diagnostics.

Vector, hybrid, ANN acceleration and persistent sidecars are accepted extensions
of this architecture, not blockers for Phase 1.
