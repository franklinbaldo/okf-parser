---
type: RFC
title: Agent-first bundle search and retrieval
status: proposed
description: Add one compact search surface over an OKF bundle, with body-line provenance, DuckDB lexical search by default, and optional vector, ANN and hybrid retrieval backends
---

# RFC 0009: Agent-first bundle search and retrieval

## Summary

`okf-parser` can validate, inspect, query and export a whole OKF bundle, but it
currently lacks the most basic operation an agent needs once a bundle becomes
larger than its useful context window: **find the small part of the bundle that
answers this question**.

This RFC adds one public retrieval primitive:

```text
search(path, query, ...)
```

The primitive is deliberately stable while the retrieval engine behind it may
vary. The baseline is lexical search over the full bundle using DuckDB. Optional
profiles may add exact vector search, approximate nearest-neighbor search,
hybrid lexical/vector ranking, persistent vector stores and rerankers without
changing the agent-facing tool.

The design has five invariants:

1. **Search the body, preserve its coordinates.** Every returned passage is
   addressable back to the Markdown body by one-based body line numbers.
2. **Compact output is the default contract.** The model should pay primarily
   for evidence, not repeated JSON keys and metadata.
3. **Lexical search is the zero-configuration default.** Embeddings are
   optional and never silently change the meaning of the default search.
4. **Indexes are derived state.** Markdown remains canonical; every index is
   rebuildable from the bundle plus an explicit retrieval profile.
5. **One tool, multiple engines.** Agents should not need separate tools for
   BM25, exact vectors, HNSW or hybrid retrieval.

The initial implementation should prioritize body-aware lexical retrieval,
compact output, filters and line provenance. Vector and ANN capabilities are
extensions of the same contract, not prerequisites for shipping `search`.

## Motivation

### Reading the whole bundle is the wrong agent primitive

An OKF bundle may contain exactly the information an agent needs while still
being too large, too repetitive or too expensive to place wholesale in the
model context. `inventory`, `graph`, generated schemas and DuckDB export help
an agent understand the bundle, but none answers a direct retrieval question
such as:

```text
Where does this bundle discuss prescricao intercorrente?
```

Without a first-class search primitive, a client must fall back to filesystem
search, custom SQL, ad-hoc embedding infrastructure, or sending substantially
more source text to the model than the task requires.

That is especially undesirable for OKF because the bundle already contains a
natural retrieval unit: the Markdown body of each concept, together with
structured metadata, stable concept identity and graph relationships.

### Retrieval cost is part of the product

For an agent, the useful cost is not merely bytes on disk or the tokenization
of the entire representation. It is the number of tokens that must actually
enter the model context before the task can be solved.

A search tool that returns this for every hit:

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

is structurally useful but unnecessarily expensive as the default model-facing
representation. The same evidence can usually be conveyed as:

```text
location	snippet
teses/prescricao.md#B35-B39	A prescricao intercorrente ocorre quando ...
recursos/apelacao.md#B81-B84	... reconhecimento da prescricao intercorrente ...
```

The ranking order already communicates relative relevance. Fields such as
score, type, title and digests remain available when requested, but should not
be repeated into ordinary agent context by default.

### The body has useful internal coordinates

`okf-parser` already parses each concept into a `body` value. Treating that body
only as an opaque string throws away a useful addressing system. The body is a
sequence of lines, and a search hit should say not only *which Markdown file*
matched but *where in its body* the useful evidence lives.

This RFC uses one-based body-relative line numbers. `B1` is the first line of
the parsed Markdown body, regardless of frontmatter length. The compact
location syntax is:

```text
relative/path.md#B<start>-B<end>
```

For example:

```text
teses/prescricao.md#B35-B39
```

Adapters may additionally expose physical source-file line numbers for editor
navigation, but body-relative coordinates are the stable retrieval coordinate
because they are directly defined by the parsed OKF concept.

## Decision

### 1. Add one public `search` capability

The CLI and MCP expose the same conceptual operation:

```text
okf-parser search PATH QUERY
```

```text
search(path, query, ...)
```

The baseline arguments are:

```text
path          bundle root
query         user search text
mode          lexical | literal | vector | ann | hybrid
limit         maximum number of returned passages
context       requested surrounding body lines
exclude       existing bundle exclusion patterns
type          optional concept-type filter
path_glob     optional path filter
detail        compact | score | full
```

`mode="lexical"` is the default even when vector infrastructure is configured.
This makes default behavior reproducible and prevents installation of an
embedding profile from silently changing search semantics.

Additional ranking knobs may be added behind explicit modes or named retrieval
profiles, but the common case should remain callable with only `path` and
`query`.

### 2. Build a derived retrieval relation with body-line provenance

Search operates over a relation derived from the already parsed bundle rather
than reparsing Markdown independently.

The implementation may use multiple internal relations, but the useful logical
model is:

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

A companion `body_lines` relation may preserve every individual line:

```text
concept_id | path | body_line | text
```

The passage relation exists for ranking quality; the line relation exists for
precise addressing and snippet construction. Search engines are therefore free
to rank paragraphs, Markdown blocks, overlapping windows or larger sections
without losing the exact body coordinates returned to the caller.

The implementation must not manufacture a snippet that cannot be mapped back
to a contiguous body line range.

### 3. Chunking is structural and replaceable

The default passage builder should respect Markdown structure before applying a
size bound. Headings, paragraphs, list blocks, blockquotes and fenced code are
better natural boundaries than blind fixed-character slicing.

A retrieval profile records the chunking strategy and its parameters. Candidate
strategies include:

- `line` -- one body line per passage;
- `paragraph` -- Markdown paragraph/block boundaries;
- `section` -- heading-delimited sections;
- `window` -- bounded overlapping line windows;
- `document` -- one concept body per passage.

The initial implementation may use a simpler line/window strategy, provided it
preserves body coordinates and does not freeze that choice into the public
`search` API.

### 4. Compact agent output is normative default behavior

The default MCP and CLI human/model-facing rendering is a compact two-column
representation:

```text
location	snippet
teses/prescricao.md#B35-B39	A prescricao intercorrente ocorre quando ...
recursos/apelacao.md#B81-B84	... reconhecimento da prescricao intercorrente ...
```

The exact alignment used by a terminal is presentation-only. The semantic
payload is `location + snippet`, ordered by relevance.

The default output intentionally omits:

- repeated `concept_id` when the path already addresses the source;
- concept type;
- title;
- raw engine score;
- ranking diagnostics;
- source and parsed digests;
- the original query;
- full document bodies.

`detail="score"` may add a compact score column. `detail="full"` may expose a
structured representation suitable for programs and debugging.

Structured internal data must remain available to Python callers, but adapters
should not force verbose JSON into the model context merely because the
implementation uses structured objects internally.

### 5. Lexical retrieval uses DuckDB first

`okf-parser` already depends on DuckDB and already exposes the bundle through
relational operations. Search should exploit that substrate instead of adding a
second mandatory search database.

The preferred lexical engine is DuckDB's `fts` extension, which provides a
full-text index and `match_bm25` ranking. It supports indexing multiple text
columns and configurable stemming, stopwords, case normalization and accent
stripping.

Official documentation:

- <https://duckdb.org/docs/current/core_extensions/full_text_search>
- <https://duckdb.org/docs/current/guides/sql_features/full_text_search>

The FTS index does not automatically track mutations of its input table. The
search implementation therefore owns refresh/invalidation and must never
present a stale derived index as current bundle state.

A no-index literal mode remains useful for exact substring/pattern retrieval and
as a minimal fallback. It may use ordinary DuckDB string predicates over the
derived body/passages relation.

### 6. Vector search does not require ANN

A configured embedding profile may materialize fixed-size vectors alongside
passages. DuckDB's fixed-size `ARRAY` type is explicitly suitable for embeddings
and has native distance/similarity functions including:

- `array_cosine_distance`;
- `array_cosine_similarity`;
- `array_distance`;
- `array_inner_product`;
- `array_negative_inner_product`.

Official documentation:

- <https://duckdb.org/docs/current/sql/data_types/array>
- <https://duckdb.org/docs/current/sql/functions/array>

Therefore `mode="vector"` can initially implement exact top-k retrieval by
scanning passage vectors and ordering by the configured metric. For small and
medium bundles this is deliberately preferable to requiring an approximate
index: it is simple, exact and has no ANN index lifecycle.

### 7. Embedding generation is a provider protocol

`okf-parser` must not make one hosted embedding vendor part of the OKF data
model. Embeddings are derived from canonical text through a configured provider.

The provider abstraction must support the semantic distinction used by modern
retrieval models:

```text
embed_documents(texts) -> vectors
embed_query(query) -> vector
```

A profile records enough metadata to reject incompatible or stale vectors:

```text
provider/model identifier
provider or model revision/fingerprint when available
dimensions
distance metric
normalization contract
chunker + chunker version
```

Providers may be local functions, local models, subprocesses, HTTP services or
hosted APIs. The core search API depends on the provider contract, not on a
particular SDK.

An external provider may have privacy, cost and network effects. Those effects
must be explicit in the configured profile and reflected in MCP annotations at
server construction time rather than hidden behind the word `search`.

### 8. ANN is an optimization of vector search

`mode="ann"` may use DuckDB's `vss` extension, which supports HNSW indexes over
`FLOAT[n]` arrays and the L2, cosine and inner-product distance families.

Official documentation:

- <https://duckdb.org/docs/current/core_extensions/vss>

The VSS extension is currently experimental, and persistent HNSW storage is
explicitly guarded by an experimental persistence setting because of recovery
limitations. `okf-parser` must therefore treat HNSW as a rebuildable
acceleration structure, not as canonical or irreplaceable storage.

The public API must not expose HNSW-specific concepts as requirements for
ordinary vector search. Parameters such as `ef_search`, `M` and
`ef_construction` belong in an ANN profile or advanced configuration.

### 9. Persistent vector stores are optional backends

Large bundles may benefit from a persistent sidecar designed for retrieval.
That sidecar remains derived state and must preserve the same passage IDs and
body-line provenance as the in-process DuckDB representation.

Lance is a strong optional candidate because DuckDB's current `lance` extension
can perform vector search, full-text search and hybrid search over Lance
datasets through SQL.

Official documentation:

- <https://duckdb.org/docs/current/core_extensions/lance>

Other stores, including SQLite-based vector extensions, may be supported by
adapters if they satisfy the same retrieval contract. They are not mandatory
runtime dependencies and do not define OKF semantics.

The backend choice must therefore be replaceable:

```text
canonical Markdown bundle
        |
        +-- ephemeral DuckDB relations
        +-- optional DuckDB vector/FTS indexes
        +-- optional Lance sidecar
        +-- future compatible sidecars
```

### 10. Hybrid retrieval is a ranking policy, not a second tool

`mode="hybrid"` combines lexical and vector candidates behind the same `search`
contract.

At least two fusion policies are plausible:

1. weighted score fusion after compatible normalization;
2. Reciprocal Rank Fusion (RRF), which combines ranks without assuming that a
   BM25 score and a vector distance share a meaningful numeric scale.

RRF is the safer baseline when heterogeneous engines are combined. A profile
may later choose another fusion policy explicitly.

Hybrid retrieval may also be staged to reduce work:

```text
BM25 top N -> vector rerank -> top K
```

or:

```text
vector top N -> structured/lexical filtering -> top K
```

The agent still calls `search` once.

### 11. Structured OKF filters apply before or during retrieval

A generic vector store knows passages. `okf-parser` knows concepts and their
structured metadata. Search should exploit that distinction.

Filters should be able to narrow candidates by at least:

- `concept_type`;
- path/path glob;
- concept identity;
- declared frontmatter fields where safely expressible;
- later, graph neighborhood or link predicates.

Whenever possible, filters are pushed down before expensive vector ranking.
This reduces both compute and irrelevant context.

### 12. Result expansion is bounded and explicit

A hit identifies the smallest useful ranked passage. The caller may request a
small amount of surrounding body context:

```text
context=2
```

A hit anchored at `B37` can then return, for example, `#B35-B39`. Expansion is
bounded by the body and must update the returned location range accordingly.

The tool must not return the full body by default. Fetching or expanding a
concept is a separate decision made after retrieval.

A later version may support an explicit output budget, for example
`max_tokens`, but the core implementation must not claim exact token budgeting
without a declared tokenizer/token-counting policy. Token-aware selection is a
retrieval policy layered over the same passage representation.

### 13. Indexes and embeddings are digest-addressed derived state

The project already computes deterministic content digests. Search indexes
should use those digests for incremental refresh.

For each indexed concept/passage:

```text
same source digest + same retrieval profile -> reuse derived rows/vectors
changed source digest                       -> rebuild affected passages
removed concept                             -> remove derived passages
new concept                                 -> add derived passages
```

The retrieval profile fingerprint must include every choice that changes vector
meaning or passage identity, including embedding model/revision, dimensions,
normalization and chunking configuration.

No `.md` source file is rewritten merely to store an embedding.

### 14. MCP effect metadata follows the configured search profile

The zero-configuration lexical search is local inspection and should be exposed
as read-only, idempotent and closed-world.

A server configured with a hosted embedding provider may perform network I/O.
RFC 0008 requires tool annotations to describe maximum possible effects. The
MCP builder must therefore annotate `search` conservatively from the configured
retrieval profile, e.g. `openWorldHint=true` when query embedding can call an
external provider.

Index construction that writes a persistent sidecar is not implicitly smuggled
into a read-only search call. Persistent index build/update operations, if
exposed as commands or MCP tools, require their own explicit effect contract.

### 15. Search becomes a benchmarkable retrieval strategy

The benchmark suite should treat compact OKF search as a first-class strategy.
For a fixed task and fixed source information, measure the tokens that actually
enter the agent context, including:

- the search-tool call arguments;
- compact search results;
- any follow-up passage/concept retrieval;
- additional tool responses needed to answer;
- the final evidence context consumed by the model.

This makes it possible to compare, for example:

```text
whole Markdown corpus
whole OKF bundle
filesystem grep
OKF lexical compact
OKF vector compact
OKF hybrid compact
```

The primary metric is task completion under actual context consumption, not
file size or tokenization of representations the agent never reads.

## Proposed implementation sequence

### Phase 1: body-aware lexical search

Ship the useful primitive before vector infrastructure:

1. derive body lines/passages from `load_bundle`;
2. preserve one-based body line ranges;
3. add DuckDB lexical/BM25 retrieval plus literal mode;
4. add concept type and path filters;
5. add compact `location + snippet` rendering;
6. expose the same capability through CLI and MCP;
7. add tests proving line provenance, deterministic ordering, bounded context
   and compact defaults;
8. add benchmark cases that count the actual search result tokens consumed by
   an agent.

### Phase 2: exact vectors

1. define the embedding-provider/profile protocol;
2. materialize passage vectors as `FLOAT[n]`;
3. use native DuckDB array distance functions for exact top-k;
4. use source/profile digests for incremental reuse;
5. keep vector search opt-in.

### Phase 3: hybrid retrieval

1. retrieve lexical and vector candidate sets;
2. implement deterministic RRF;
3. push structured filters before expensive ranking;
4. measure quality and agent-token cost against lexical-only retrieval.

### Phase 4: ANN and persistent sidecars

1. add optional DuckDB VSS/HNSW acceleration;
2. benchmark recall/latency against exact vector search;
3. evaluate Lance as the preferred persistent large-bundle backend;
4. keep every index rebuildable and provenance-preserving.

### Phase 5: advanced policies

Candidates, only after measurement justifies them:

- reranker providers;
- token-budget-aware result packing;
- graph-neighborhood expansion;
- diversity/MMR-style selection;
- multiple named embedding profiles for the same bundle;
- query classification/routing between literal, lexical, vector and hybrid
  modes.

None requires a new agent-facing search tool.

## Non-goals

This RFC does not:

- make embeddings part of the OKF canonical format;
- require network access to search a bundle;
- require a hosted embedding vendor;
- require VSS/HNSW;
- turn `okf-parser` into a general-purpose vector database;
- promise semantic search in the initial implementation;
- require full document bodies in search results;
- define one permanent chunking algorithm;
- make a persistent sidecar authoritative over Markdown;
- automatically execute external embedding calls without explicit
  configuration.

## Alternatives considered

### Separate MCP tools for each algorithm

For example `search_bm25`, `search_vector`, `search_hnsw` and `search_hybrid`.
This leaks implementation detail into the agent's tool-selection problem,
increases schema/context overhead and makes backend evolution harder. Rejected
in favor of one `search` tool with explicit mode/profile selection.

### JSON-only output

JSON is convenient for programs but unnecessarily verbose for the dominant
agent-reading case. Rejected as the default. Full structured detail remains
opt-in.

### Whole-document embeddings only

This loses precise evidence locality and forces the agent to consume an entire
concept after retrieval. Rejected as the default representation. Passage
embeddings must preserve body line ranges.

### Line embeddings only

Line granularity gives excellent coordinates but often destroys enough context
to hurt semantic ranking. Rejected as the only chunking strategy. The design
keeps a line relation for addressing while allowing larger structural passages
for ranking.

### Dedicated vector database as a mandatory dependency

This adds deployment complexity before the project has demonstrated that exact
DuckDB vector search is insufficient. Rejected. Specialized persistent stores
remain optional backends.

### HNSW as the canonical vector representation

ANN indexes trade exactness for speed and have their own lifecycle and
persistence constraints. Rejected. HNSW is an acceleration structure over
derived vectors.

## Open questions

Implementation work should answer these empirically rather than freezing them
prematurely in the public contract:

1. What default passage strategy gives the best retrieval quality per returned
   token for typical OKF bundles?
2. Should lexical Phase 1 index passages, body lines, or both?
3. What compact snippet width/context default minimizes follow-up calls without
   bloating first-pass results?
4. At what corpus size does DuckDB exact vector scan become materially worse
   than HNSW for expected bundle workloads?
5. Does RRF materially outperform simpler staged lexical/vector reranking for
   the benchmark tasks?
6. Should a persistent retrieval profile live in a bundle-local config file,
   project configuration, or remain caller-supplied until usage stabilizes?
7. Which tokenizer contract, if any, is appropriate for an exact
   `max_tokens` result budget across heterogeneous agent models?

These are benchmark questions. They should not block the Phase 1 search
primitive.

## Acceptance criteria

This RFC is successfully implemented when the baseline release can demonstrate:

1. `okf-parser search PATH QUERY` and MCP `search(path, query)` over the whole
   bundle;
2. default lexical retrieval with no embedding provider;
3. each hit mapped to an exact contiguous one-based body line range;
4. default compact output containing only location and useful snippet text;
5. explicit opt-in for richer metadata;
6. deterministic ranking/tie-breaking for a fixed bundle and configuration;
7. bounded result count and context expansion;
8. no canonical Markdown mutation as a side effect of search;
9. tests for multilingual/accented body text, line provenance, exclusions and
   filters;
10. a benchmark recording actual agent-consumed tokens for compact OKF search.

Vector, ANN, hybrid and persistent-sidecar support are accepted extensions of
this architecture, but are not blockers for the first implementation.