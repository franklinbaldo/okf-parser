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
relational substrate and its `fts` extension is a preferred lexical optimization
when it is already available locally. Embeddings, vector indexes, approximate
nearest-neighbor acceleration, persistent sidecars and rerankers are optional
profile choices; they do not create new agent-facing tools.

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

The initial implementation is intentionally narrow: body-aware lexical and
literal retrieval, compact rendering, deterministic ordering, filters and exact
body provenance. Vector and hybrid retrieval extend the same contract later.

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

Phase 1 factors the existing `concept.body.splitlines()` behavior into shared
bundle/search support. That behavior becomes a language-neutral conformance
contract: other runtimes reproduce the checked-in fixtures rather than using a
runtime-native line splitter that happens to be similar. Search may build
additional passage relations for ranking, but body-line coordinates always come
from that shared representation.

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
    Maximum returned hits after filtering and base-range deduplication. Must be
    >= 1.

context: int = 0
    Extra whole body lines on each side of each selected hit. Must be >= 0.

exclude: list[str] = []
    Existing bundle exclusion patterns.

concept_type: str | None = None
    Optional exact producer-defined concept type filter.

path_glob: str | None = None
    Optional positive glob over raw bundle-relative logical paths already
    discovered in the bundle.

detail: "compact" | "score" | "full" = "compact"
    Agent-oriented compact rendering, compact rendering with score, or full
    structured result.

profile: str | None = None
    Optional named retrieval profile. Backend/index choices live here rather
    than in the agent-facing mode.
```

`mode` is authoritative user intent. A profile may choose a backend, chunker,
scorer, embedding provider, index or acceleration strategy **within the
requested mode**, but it must never change the mode itself.

Profile resolution follows these rules:

1. `profile=None` selects the configured default profile for the requested
   mode;
2. the default `lexical` profile must satisfy the offline, closed-world contract
   in this RFC;
3. a named profile must exist and declare compatibility with the requested
   mode;
4. an unknown or incompatible profile is an explicit error;
5. failure to resolve a vector/hybrid implementation is an explicit
   unsupported/unconfigured error, never a silent fallback to lexical, literal
   or a hosted provider;
6. merely installing or configuring embeddings never changes
   `mode="lexical"` or its zero-configuration behavior.

Phase 1 implements `lexical` and `literal`. The public enum reserves `vector`
and `hybrid` so later phases extend the same tool, but a Phase 1 runtime must
fail clearly when either is requested and no compatible implementation/profile
exists.

`concept_type` compares against the exact authored `type` value represented by
the bundle relation. It is case-sensitive and performs no slugging, case
folding, accent folding or Unicode normalization.

`path_glob` operates on the raw bundle-relative POSIX logical path, before any
compact-location escaping. It uses the same **positive path-pattern grammar**
as one RFC 0004 `.okfignore` pattern: `*` and `?` stay within one path segment,
character classes follow the RFC 0004 matcher, and `**` may span segments. It is
an inclusion filter: only matching discovered paths remain candidates. Leading
`!` negation, comments and blank-line syntax are rejected because `path_glob`
is one positive filter, not an ignore file.

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
`detail="full"` identifies the source input from which the parsed body and its
location were produced.

The path component of a compact location must remain one physical output field.
For compact rendering only, it is UTF-8 percent-encoded as follows:

- `%` -> `%25`;
- `#` -> `%23`;
- ASCII control bytes `00`-`1F` and `7F` -> `%XX` with uppercase hex;
- all other UTF-8 text, including `/` and ordinary Unicode characters, is
  emitted unchanged.

This escaping is part of compact serialization, not path identity. Filters and
`detail="full"` use the raw logical path.

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

`passage_id` is internal derived state. It is not an observable identity and
must never be needed to make public ordering deterministic.

The passage relation exists for ranking quality; the line relation exists for
precise addressing and snippet construction. Paragraphs, Markdown blocks,
overlapping windows or sections may be ranked without losing exact body
coordinates.

Every returned hit must map to a contiguous range in the shared body-line
representation. Before `limit` is applied, duplicate engine candidates with the
same raw `(path, body_start_line, body_end_line)` are coalesced, retaining the
best score for that range.

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

The default model-facing rendering is a compact two-column TSV form:

```text
location\tsnippet
teses/prescricao.md#B35-B36\tPrescricao intercorrente. O prazo volta a correr.
recursos/apelacao.md#B81\tO recurso sustenta o reconhecimento da prescricao.
```

The header is normative. Each hit occupies exactly one physical output line.

The compact snippet transformation is a closed whitelist, not an open-ended
whitespace normalization rule:

1. take the complete text of every body line named by the returned range, in
   order;
2. replace each literal tab character inside a body line with one ASCII space;
3. join adjacent body lines with exactly one ASCII space;
4. preserve every other character in every body line exactly, including
   leading spaces, trailing spaces and repeated internal spaces.

No other whitespace collapsing, trimming, Unicode normalization or textual
rewriting is permitted in compact rendering. Phase 1 never cuts inside a
selected body line, and the renderer never omits a prefix or suffix of a
selected line while retaining a location that claims the whole line.

If a ranked passage is too large for the desired compact retrieval behavior,
the ranking/chunking layer must choose a smaller **contiguous set of whole body
lines before rendering**. It must not truncate text inside a line and then
reuse the old location.

`detail="score"` uses exactly three columns:

```text
location\tscore\tsnippet
```

Scores are finite JSON numbers. Their text rendering is frozen by shared
conformance fixtures. A score is an opaque ranking diagnostic: larger means
better **within the same search call**, but scores are not comparable across
modes, profiles or resolved engines.

`detail="full"` returns a structured object. Its stable core contains the
request semantics and provenance-bearing results; backend diagnostics are not
part of the semantic contract. A canonical full result is:

```json
{
  "query": "prescricao intercorrente",
  "mode": "lexical",
  "profile": null,
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
  ],
  "diagnostics": {
    "engine": "duckdb_fts"
  }
}
```

In `detail="full"`, `path` is the raw logical path. `location` uses the same
compact-location escaping contract. `body_start_line` and `body_end_line`
describe the range actually returned after `context` expansion. `text` is the
selected logical body lines joined with `\n`, preserving the characters inside
each logical line; it is not a byte-for-byte source slice and does not claim to
preserve original line-ending spelling.

`diagnostics` is explicitly non-semantic. Implementations may add or omit
backend/profile-fingerprint diagnostics there for debugging and benchmark
reproducibility. Programs requiring portable behavior must not branch on an
engine name such as `duckdb_fts`, and cross-runtime parity does not require the
same diagnostic keys or backend names.

There is no `max_snippet_tokens` contract in Phase 1. Token-aware clipping can
be added only after the project chooses a tokenizer/counting contract and a
representation that preserves truthful provenance.

### 6. Lexical search is offline by contract; local FTS is an optimization

`mode="lexical"` means ranked lexical relevance over candidate passage text. It
is distinct from literal substring matching. Tokenization, stemming and the
exact relevance formula may vary by compatible retrieval profile/engine; those
choices do not create new public modes.

DuckDB's `fts` extension provides full-text indexes and BM25 ranking and is a
preferred lexical implementation when the extension is already installed and
loadable locally.

Official documentation:

- <https://duckdb.org/docs/current/core_extensions/full_text_search>
- <https://duckdb.org/docs/current/extensions/overview>

DuckDB can autoinstall/autoload known extensions, which may require network
access on first use. Therefore default `okf-parser search` must not rely on that
path.

For the default local/closed-world search path:

1. a deterministic built-in lexical scorer is always sufficient to make
   `mode="lexical"` work;
2. an implementation may use `fts` only when it is already available locally;
3. search must not issue `INSTALL fts` as an implicit side effect;
4. search must not trigger an extension autoinstall/download path merely by
   searching;
5. search must not require network access or a persistent sidecar;
6. if local `fts` cannot be used safely, search uses the built-in scorer rather
   than failing or downgrading to `mode="literal"`.

The built-in lexical scorer is the cross-runtime baseline. Its Phase 1 formula
and ordering are frozen by conformance fixtures when implemented. Local FTS is
an optional optimization from Phase 1 onward and is **not required to ship the
first useful Phase 1 lexical slice**.

If FTS is used, search owns its ephemeral refresh/invalidation and must never
present a stale index as current bundle state. Building or updating a persistent
index is a different effectful capability, not an implicit side effect of
`search`.

Resolved engine information may be reported only as non-semantic diagnostics
under `detail="full"`.

### 7. Literal mode has exact matching and ordering semantics

`mode="literal"` means literal substring matching over each candidate passage's
logical text before compact rendering. It is not a regular-expression mode and
performs no stemming, token expansion, accent folding or fuzzy matching.

Case-insensitivity uses Unicode Default Case Folding on both query and candidate
text. No additional Unicode normalization is performed. The release's observable
behavior is frozen by shared multilingual conformance fixtures so Python and
TypeScript do not independently substitute their runtime-native lowercase
operation.

Every matching literal base range has score `1`. Literal results therefore use
the structural tie-break in section 8. `detail="score"` is valid and renders
that score like any other mode.

Regex search may be added later under an explicit semantic rather than changing
`literal` behavior.

### 8. Ranking, deduplication and tie-breaking are deterministic

Every engine normalizes its result ordering into a rank where the best result is
first. When a numeric score is exposed, larger means better within that search
call.

Engines first coalesce duplicate raw hit ranges as described in section 3.
After that, equal scores use this canonical tie-break:

```text
(score DESC, path ASC, body_start_line ASC, body_end_line ASC)
```

`path` in this ordering is the raw bundle-relative logical path, compared by
Unicode scalar value/code point order as frozen by conformance fixtures. No
internal `passage_id` participates in public ordering.

For a fixed bundle, requested mode, resolved profile, resolved engine contract
and engine availability, result order must be deterministic.

Different compatible engines are allowed to produce different lexical/vector
relevance scores and therefore different relevance ordering. This does not
weaken the public mode contract: all engines must preserve provenance, best-first
rank semantics, finite higher-is-better scores when exposed, deduplication and
the same structural tie-break.

Cross-runtime **ranking parity** is required only when both runtimes execute the
same resolved engine/profile contract. Shared fallback lexical fixtures provide
the engine-independent baseline required by RFC 0002. Engine-specific fixtures
may additionally pin behavior for the same DuckDB/extension contract. Two
machines resolving different engines are not required to return numerically
identical scores or rank order merely because both calls said
`mode="lexical"`.

### 9. Vector search does not expose ANN as user intent

A configured embedding profile may materialize fixed-size vectors alongside
passages. DuckDB fixed-size arrays support native distance/similarity functions,
so `mode="vector"` can begin with exact top-k scanning.

Exact versus approximate execution is deliberately hidden behind the retrieval
profile. Public examples should use intent-oriented profile names, for example:

```text
mode="vector", profile="legal-local"
mode="vector", profile="legal-fast"
mode="vector", profile="legal-large"
```

One profile may internally use exact DuckDB distance, another HNSW/VSS, and
another Lance. Those backend names are configuration/diagnostic details, not
public mode values.

Parameters such as `ef_search`, `M` and `ef_construction` belong to profile
configuration, not the public `mode` enum.

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
explicit profile properties and feed the **static** MCP annotation calculation
in section 16; annotations do not change per invocation.

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

A `search` invocation may consume already-built derived state, but it must not
silently create or update a persistent sidecar. Persistent index build/update,
if exposed, is a separate capability with its own effect contract.

### 13. Structured OKF filters have exact public semantics

Search should exploit the fact that OKF knows concepts and structured metadata.
The Phase 1 public filters are exactly:

- `concept_type`, with the exact authored-value semantics from section 1;
- `path_glob`, with the positive RFC 0004 path-pattern semantics from section 1;
- existing bundle `exclude` patterns, applied during ordinary bundle discovery.

Concept identity, arbitrary declared frontmatter predicates and graph/link
predicates remain possible future filters, but they are not silently implied by
the Phase 1 schema.

Filters apply before ranking whenever the engine supports doing so without
changing semantics; otherwise they apply before `limit`. The observable result
must be the same either way.

### 14. `context` expands after ranking and preserves whole-line provenance

A hit identifies a useful ranked base passage. The caller may request
surrounding body context:

```text
context=2
```

`context=N` adds up to `N` immediately preceding body lines and up to `N`
immediately following body lines to each selected base hit. Expansion is
bounded by the concept body.

The operation order is normative:

1. discover and filter candidates;
2. rank and coalesce duplicate base ranges;
3. select up to `limit` base hits;
4. expand each selected hit by `context`;
5. render the expanded range.

Context therefore does not affect score or ranking and does not increase the
number of hits. The returned location, `body_start_line`, `body_end_line`,
`snippet` and full-detail `text` all describe the expanded range exactly.
Distinct ranked hits may overlap after expansion; the RFC does not merge those
hits implicitly because doing so would change rank identity and hit count.

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

### 16. MCP annotations are static and conservative under RFC 0008

RFC 0008 requires annotations to describe the **maximum possible effect of the
public tool**, not the effect of the safest argument combination. Search follows
that rule.

MCP annotations therefore do **not** "follow the resolved profile" per call.
Instead, when an MCP server registers `search`, it computes one static annotation
set that conservatively covers every profile selectable through that tool in
that server instance.

For the Phase 1 server, where the selectable search behavior is local lexical
and literal inspection only, the tool can advertise:

```text
readOnlyHint=true
destructiveHint=false
idempotentHint=true
openWorldHint=false
```

Future profile effects are combined conservatively:

- if any selectable profile can call a hosted provider or otherwise cross the
  closed local domain, `openWorldHint=true` for the whole `search` tool;
- if any selectable profile can make repeated calls observably non-idempotent,
  the tool must advertise `idempotentHint=false`;
- if a future legal search profile can mutate environment state, `readOnlyHint`
  must reflect that maximum effect rather than the lexical default.

However, this RFC separately forbids `search` itself from silently installing
extensions or creating/updating a persistent sidecar. Persistent index mutation
belongs in a separate capability, so adding such a capability does not by itself
make ordinary search mutating.

The set of profiles used for annotation calculation is fixed when the MCP tool
is registered. If server configuration changes which profiles are selectable,
the server must rebuild/reannounce the tool metadata before those profiles can
be invoked. Per-call annotation mutation is not a valid implementation.

### 17. Runtime ownership and cross-runtime parity are explicit

The search contract is runtime-neutral. Location syntax and escaping, compact
rendering, filter semantics, literal case folding, argument errors, base
fallback ordering and shared conformance fixtures belong to the observable
contract established by RFC 0002.

Phase 1 implementation ownership is:

- **Python:** the first implementation lives in the existing Python service
  layer, preferably a focused search module called by `service.py`, with CLI and
  MCP adapters. The deterministic built-in lexical scorer lives here first;
  local DuckDB FTS may be added as an optimization without changing the API.
- **Rust core:** Rust continues to provide parsing/bundle acceleration where
  already used. Phase 1 does not add a second Rust search engine.
- **TypeScript core:** no independent search semantics are invented; shared
  location/output/filter/literal/fallback fixtures are added to `conformance/`
  with the Python work.
- **`okf-parser-duckdb` (`typescript-duckdb`):** DuckDB-specific TypeScript
  search parity belongs in this adapter when implemented, reusing compatible
  engine-specific conformance cases.

Python delivery may land before the TypeScript adapter implementation,
consistent with RFC 0002's incremental parity model. No release should claim
cross-runtime search parity until both implementations pass the shared search
conformance cases for the capabilities that release claims.

Backend diagnostic identity is not part of RFC 0002 parity. Ranking equality is
required only under the same resolved engine/profile contract as defined in
section 8.

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

Score detail:

```text
location\tscore\tsnippet
teses/prescricao.md#B35-B36\t8.31\tPrescricao intercorrente. O prazo volta a correr.
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
  "profile": null,
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
  ],
  "diagnostics": {
    "engine": "duckdb_fts"
  }
}
```

Canonical argument/configuration errors include:

```text
search(path=".", query="   ")
-> error: query must not be empty
```

```text
search(path=".", query="prazo", limit=0)
-> error: limit must be at least 1
```

```text
search(path=".", query="prazo", context=-1)
-> error: context must be non-negative
```

```text
search(path=".", query="prazo", mode="lexical", profile="vector-only")
-> error: profile is incompatible with mode lexical
```

```text
search(path=".", query="prazo", mode="vector")
-> error: vector retrieval is not configured
```

Exact diagnostic codes/messages should be frozen with the implementation's
shared conformance cases. The semantics above are normative; one runtime must
not silently reinterpret an invalid combination that another rejects.

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

### Phase 1A: useful body-aware lexical search

This is the smallest slice required before agents can use the feature:

1. factor/reuse and fixture the existing body-line splitting semantics;
2. add body/passages relations with one-based coordinates;
3. implement the deterministic built-in offline lexical scorer;
4. add Unicode-case-folded literal substring mode;
5. implement exact `concept_type`, `path_glob` and existing exclusion filters;
6. implement compact whole-line snippet rendering and location escaping;
7. implement base-range deduplication, deterministic tie-breaking and
   `context` expansion;
8. implement `compact`, `score` and stable-core `full` detail;
9. implement explicit profile/mode resolution errors;
10. expose one `search` capability through Python service, CLI and MCP with the
    Phase 1 static effect annotations;
11. add shared conformance fixtures for line splitting, paths, filters,
    multilingual literal matching, locations and compact/full output;
12. benchmark actual agent-consumed search-result tokens.

Phase 1A has no embedding, vector, ANN, persistent-sidecar or extension-download
dependency.

### Phase 1B: optional local DuckDB FTS optimization

After the useful offline baseline exists:

1. detect whether DuckDB `fts` is already locally loadable without autoinstall;
2. use local FTS/BM25 when safe and useful;
3. fall back to the built-in scorer without changing public mode semantics;
4. own ephemeral FTS refresh/invalidation;
5. add engine-specific tests/benchmarks while keeping backend diagnostics
   non-semantic.

Phase 1A is sufficient to begin and ship useful lexical search; Phase 1B is an
optimization and does not gate the public contract.

### Phase 2: exact vectors

1. define the embedding provider/profile protocol;
2. materialize passage vectors as fixed-size arrays;
3. use native DuckDB distance functions for exact top-k;
4. use source/profile digests for incremental reuse;
5. keep vector search opt-in and fail clearly when unconfigured.

### Phase 3: hybrid retrieval

1. retrieve lexical and vector candidate sets;
2. implement deterministic RRF or another measured fusion policy;
3. push structured filters before expensive ranking;
4. measure quality and agent-token cost against lexical-only retrieval.

### Phase 4: indexed acceleration and persistent sidecars

1. add optional HNSW/VSS as a vector profile/backend optimization;
2. benchmark recall/latency against exact vector search;
3. evaluate Lance and compatible sidecars for large bundles;
4. keep every index rebuildable and provenance-preserving;
5. expose any persistent build/update operation separately from `search` with
   its own RFC 0008 effect contract.

### Phase 5: TypeScript search parity

1. implement the shared search contract in the appropriate TypeScript surface;
2. put DuckDB-specific search in `typescript-duckdb`;
3. run the same language-neutral fallback/output/filter fixtures;
4. run engine-specific fixtures only where both runtimes resolve the same
   engine contract;
5. document when cross-runtime search parity is achieved.

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
- require DuckDB FTS merely to make lexical search work;
- require VSS/HNSW;
- expose ANN/HNSW as a public retrieval semantic;
- turn `okf-parser` into a general-purpose vector database;
- promise semantic/vector search in Phase 1;
- silently reinterpret vector/hybrid requests as lexical search;
- return full document bodies by default;
- define one permanent chunking algorithm;
- make a persistent sidecar authoritative over Markdown;
- make body-line locations stable across edits;
- define arbitrary `#B...` range parsing in Phase 1;
- silently install extensions or execute hosted embedding calls;
- mutate a persistent retrieval store as an implicit side effect of `search`;
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
search. Rejected. The deterministic built-in scorer is sufficient; local FTS is
an optional optimization.

### Backend names in the stable full-detail schema

A stable field such as `engine="duckdb_fts"` would make a diagnostic execution
choice look like a portable retrieval semantic. Rejected. Backend identity may
appear under non-semantic `diagnostics`, while mode/profile and provenance form
the portable contract.

### Dynamic MCP annotations per selected profile

MCP annotations are static tool metadata under RFC 0008. Advertising a local
lexical call as closed-world and then changing the same tool's annotation when a
hosted profile is selected would contradict that contract. Rejected. A server
advertises the conservative union of every profile selectable through that
registered tool.

### Intra-line snippet truncation

Cutting a line around a high-scoring span while returning a location for the
whole line makes the provenance claim false. Rejected for Phase 1. Compact
snippets render whole body lines; smaller results come from selecting a smaller
contiguous line range before rendering.

### Open-ended compact whitespace normalization

Allowing implementations to "normalize whitespace" after selecting whole body
lines makes `location + snippet` ambiguous and breaks cross-runtime fidelity.
Rejected. Compact rendering has the closed transformation whitelist in section
5.

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
3. What deterministic built-in lexical scoring formula gives the best useful
   baseline while remaining cheap and cross-runtime reproducible?
4. At what corpus size does exact vector scan become materially worse than an
   ANN profile?
5. Does RRF materially outperform staged lexical/vector reranking?
6. Where should named retrieval profiles live?
7. Which tokenizer contract, if any, should govern a future exact output-token
   budget without weakening body-line provenance?
8. When should Rust take on retrieval preprocessing, if profiling demonstrates
   Python-side passage construction is material?

These questions do not block Phase 1A because none changes the public semantics
frozen above. The built-in scorer and initial chunker are implementation choices
to be selected and pinned by the Phase 1 conformance fixtures, not new public
arguments.

## Acceptance criteria

The Phase 1A baseline is complete when it demonstrates:

1. `okf-parser search PATH QUERY` and MCP `search(path, query)` over a bundle;
2. a public mode enum limited to `lexical`, `literal`, `vector` and `hybrid`,
   with lexical/literal implemented and explicit unconfigured errors rather than
   fallback for unavailable vector/hybrid modes;
3. default lexical retrieval with no embedding provider, persistent sidecar,
   extension installation or required network access;
4. a deterministic built-in lexical fallback whose scoring/order is frozen by
   shared conformance fixtures;
5. each hit mapped to an exact contiguous one-based body-line range using the
   shared line-splitting contract;
6. explicit warning/contract that body locations are ephemeral across edits;
7. compact one-physical-line-per-hit `location + snippet` output with canonical
   path escaping;
8. compact snippets containing the complete text of the body lines named by
   their location, using only the closed whitespace transformation from section
   5 and no intra-line truncation;
9. exact `context=N` semantics: up to N lines before and N after, applied after
   ranking/limit without changing score;
10. explicit `detail="score"` column order and a stable-core
    `detail="full"` structured output with backend identity confined to
    non-semantic diagnostics;
11. deterministic base-range deduplication and ordering by score, raw path,
    body start and body end, with no internal `passage_id` tie-break;
12. `concept_type` exact-value semantics and `path_glob` positive RFC 0004
    semantics;
13. explicit unknown/incompatible profile and unavailable-mode errors, with
    `mode` authoritative over profile;
14. Phase 1 MCP annotations computed statically for the maximum selectable tool
    effect, consistent with RFC 0008;
15. no canonical Markdown mutation and no implicit persistent index write;
16. shared conformance fixtures covering line splitting, location escaping,
    compact/score/full rendering, exclusions, filters, argument errors,
    profile/mode errors and multilingual Unicode case folding;
17. a benchmark recording actual agent-consumed tokens as the primary cost
    metric, with recall/rank quality/latency as secondary diagnostics.

Local DuckDB FTS/BM25 use is an accepted Phase 1B optimization when already
available without implicit install/download. It is not a blocker for a useful
Phase 1A implementation. Vector, hybrid, ANN acceleration and persistent
sidecars are later accepted extensions of the same public contract.