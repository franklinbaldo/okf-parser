---
type: RFC
title: Agent-first bundle search and retrieval
status: draft
rfc_status: proposed
description: Define one compact, provenance-preserving search surface over an OKF bundle, with offline lexical search by default and optional vector, hybrid, and indexed retrieval profiles
---

# RFC 0016: Agent-first bundle search and retrieval

## Summary

`okf-parser` needs one agent-facing operation for finding the smallest useful evidence in a bundle without placing the whole corpus in model context:

```text
search(path, query, ...)
```

The public operation is stable while retrieval implementations may evolve behind named profiles. The baseline is local lexical search over the parsed OKF bundle. Markdown remains canonical. Search results point back to exact contiguous ranges of one-based Markdown body lines, rendered compactly as ephemeral evidence locations such as `path.md#B35-B39`.

Phase 1 deliberately requires no embeddings, vector database, persistent sidecar, extension download, or network access. A deterministic built-in lexical scorer is sufficient. DuckDB FTS/BM25 is an optional local optimization when the extension is already available without installation or download.

The public retrieval modes are exactly:

```text
lexical | literal | vector | hybrid
```

Exact vector scan, HNSW/VSS, Lance, embedding providers, chunkers, rerankers, and storage engines are profile/backend choices, not additional public modes or tools.

The design has seven invariants:

1. **One public search operation.** Backend choice does not become agent tool-selection work.
2. **Markdown evidence remains ordinary Markdown.** Internal structural richness is used to select evidence, not dumped into model context.
3. **Body coordinates are exact but ephemeral.** `#B...` identifies the current parsed body, not a durable semantic anchor.
4. **Compact output is normative.** The default result spends context primarily on evidence rather than repeated metadata.
5. **Offline lexical search always works.** Default search never implicitly installs an extension, downloads anything, calls a hosted embedding provider, or writes a persistent sidecar.
6. **Indexes and embeddings are derived state.** They are rebuildable from canonical source plus explicit retrieval configuration.
7. **Agent-context tokens are the primary efficiency metric, subject to task success/quality.** Recall, MRR, latency, and build cost are diagnostics, not substitutes for the actual context cost paid by the agent.

## Relationship to adjacent RFCs

### RFC 0015 — canonical Markdown document IR

RFC 0015 is the authoritative structural boundary for Markdown. Its normalized-source digest, spans, `SourceMap`, sections, and document IR are the long-term authority for structural passage selection.

RFC 0016 does **not** turn that IR into agent output. Structural retrieval should use the document IR to select the smallest complete useful subtree/section and then return ordinary Markdown evidence. Raw AST/IR JSON is not the default agent context.

The compact body-line location remains a presentation/evidence coordinate. It is intentionally weaker than a structural selector:

```text
RFC 0015 document IR + spans
        ↓ select useful complete structure
body-line range in current parsed body
        ↓ compact render
path.md#B35-B39 + Markdown snippet
```

Phase 1A may begin with the current shared body-line projection and a simple line/window passage builder. It does not need to wait for every structural retrieval helper envisioned by RFC 0015. When structural selection is available, it replaces passage selection internals without changing the public `search` contract.

### RFC 0002 — Python/TypeScript observable parity

Search is runtime-neutral. Query trimming, location syntax and escaping, compact rendering, literal semantics, filters, argument/configuration errors, and the built-in fallback ranking contract are shared observable behavior and belong in language-neutral conformance fixtures.

Python may land first under RFC 0002's incremental parity model. A release must not claim cross-runtime search parity until the corresponding TypeScript implementation passes the shared cases.

### RFCs 0005/0006 — relational bundle and DuckDB

Search is a consumer of the existing parsed bundle/relational boundary. It does not create a second parser, scanner, concept identity system, or frontmatter projection.

DuckDB remains a useful relational substrate. Search may derive passage relations and may use local DuckDB FTS when safely available, but canonical Markdown and bundle identity remain upstream of those materializations.

### RFC 0008 — effect-aware MCP tools

MCP annotations are static metadata describing the maximum possible effect of the registered tool. They do not change per call based on `profile`.

Phase 1 lexical/literal search is local inspection and can be registered read-only, idempotent, and closed-world. If a future server instance exposes any selectable search profile that can call a hosted provider or otherwise cross the closed local domain, the single registered `search` tool must conservatively advertise that maximum effect.

### Proposed RFC 0012 / PR #178 — unified service ownership

If RFC 0012 is adopted, search is a sibling consumer of the canonical bundle/service boundary it defines, never a second filesystem scanner or parser. Until then, Phase 1A uses the current Python service layer and loaded bundle. Adoption of RFC 0012 may move ownership/wiring without changing this public contract.

### Proposed RFC 0014 / PR #184 — hybrid materialization lifecycle

If RFC 0014 is adopted, persistent FTS/vector/Lance state created for search must use that shared workload-specific materialization lifecycle: derived, disposable, snapshot-addressed, rebuildable, and backend-hidden. Search must not invent a parallel cache/index lifecycle.

This does not block Phase 1A, whose required retrieval state may remain ephemeral/in-process.

### Evaluation work / PR #187

The search benchmark must compare strategies on equivalent tasks and source information. Task success/correctness is the validity gate. Among strategies that solve the task acceptably, the primary efficiency metric is how many tokens actually enter agent context before the answer can be produced. Retrieval quality and latency remain secondary diagnostics.

## Decision

### 1. Public schema

The conceptual schema is:

```text
path: str
    Bundle root.

query: str
    Search text. Before validation remove only ASCII SP, HT, LF, VT, FF, and CR
    from both ends. The remaining query must be non-empty. Other Unicode
    whitespace is ordinary query content.

mode: "lexical" | "literal" | "vector" | "hybrid" = "lexical"
    User retrieval intent. Exact/approximate execution is not a public mode.

limit: int = 10
    Maximum returned hits after filtering and final-range deduplication.
    Must be >= 1.

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
    Compact evidence, compact evidence plus score, or structured diagnostics.

profile: str | None = None
    Optional named retrieval profile. Backend/index/provider/chunker choices
    live here rather than in mode.
```

CLI and MCP expose the same semantics:

```text
okf-parser search PATH QUERY
search(path, query, ...)
```

### 2. `mode` is authoritative; `profile` cannot rewrite intent

A profile may choose a backend, chunker, scorer, embedding provider, index, or acceleration strategy **within the requested mode**. It must never silently change the mode itself.

Resolution rules are normative:

1. `profile=None` selects the default profile for the requested mode;
2. Phase 1 needs no external profile registry: lexical/literal defaults may be built in;
3. the default lexical profile must satisfy the offline closed-world contract in this RFC;
4. a named profile must exist and declare compatibility with the requested mode;
5. an unknown or incompatible profile is an explicit error;
6. failure to resolve vector/hybrid is an explicit unsupported/unconfigured error, never a fallback to lexical/literal or an implicit hosted provider;
7. installing/configuring embeddings never changes default `mode="lexical"` behavior.

A Phase 1 implementation may therefore accept only `profile=None` for lexical/literal and reject every non-null profile as unconfigured. Reserving the argument does not force Phase 1 to invent a profile configuration file prematurely.

Phase 1 implements lexical and literal. `vector` and `hybrid` are reserved public intents for later phases so the tool schema does not need an algorithm-named expansion. A Phase 1 runtime must fail clearly when either is requested and unavailable.

### 3. Filter semantics are exact

`concept_type` compares against the exact authored `type` value represented by the bundle relation. It is case-sensitive and performs no slugging, case folding, accent folding, or Unicode normalization.

`path_glob` operates on the raw bundle-relative POSIX logical path before location escaping. Its wildcard and anchoring semantics reuse the path-matching portion of RFC 0004:

- no `/`: may match a basename at any depth;
- leading or internal `/`: anchored at bundle root;
- `*` and `?`: do not cross path segments;
- RFC 0004 character classes are supported;
- `**`: may span segments.

`path_glob` is one positive inclusion filter, not an ignore-file line. `!` and `#` are ordinary literal characters, whitespace is part of the pattern, and ignore-file comment/negation/trailing-space control syntax does not apply. Because search candidates are files, a directory-only pattern does not itself produce a file candidate.

Existing `exclude` patterns retain their ordinary bundle-discovery semantics.

The Phase 1 public filters are exactly `concept_type`, `path_glob`, and existing `exclude`. Concept identity, arbitrary frontmatter predicates, and graph predicates may be added later explicitly; they are not implicit Phase 1 behavior.

### 4. Body-relative locations are exact and ephemeral

Body line numbers are one-based. `B1` is the first line of the parsed Markdown body regardless of frontmatter length.

Canonical locations are:

```text
relative/path.md#B37
relative/path.md#B35-B39
```

A one-line hit uses `#B37`; a multi-line hit uses `#B<start>-B<end>`.

The location identifies evidence in the **current parsed body only**. It is not a concept identifier, durable anchor, structural selector, or reference to persist across edits.

`detail="full"` includes `source_digest`, identifying the authored source input from which the parsed body and location were produced. RFC 0015's structural selectors/digests remain the authority for future guarded structural operations; this RFC does not overload `#B...` with that job.

This RFC defines locations emitted by search. It does not define a general parser or follow-up read API for arbitrary user-supplied `#B...` ranges.

### 5. Compact-location path escaping

The path component must remain one physical output field. For compact locations only, encode:

- `%` as `%25`;
- `#` as `%23`;
- ASCII control bytes `00`-`1F` and `7F` as uppercase `%XX`;
- all other UTF-8 text, including `/` and ordinary Unicode, unchanged.

Escaping is serialization, not path identity. Filtering, ranking tie-breaks, and full-detail `path` use the raw logical path.

### 6. Search reuses shared body lines

Search operates over the already parsed bundle and must reuse one shared body-line construction. Phase 1 factors the current `concept.body.splitlines()` behavior into reusable support and freezes it with language-neutral fixtures rather than letting each runtime choose a similar native splitter independently.

A logical body-line relation is:

```text
concept_id | path | body_line | text
```

Ranking may use a derived passage relation such as:

```text
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

`passage_id` is internal derived state. It is not a public identity and must not be needed to make observable ordering deterministic.

Every returned hit maps to one contiguous range in the shared body-line representation.

### 7. Passage construction is replaceable

The passage builder should prefer Markdown structure when available, but the public API does not expose a chunker name.

Possible internal strategies include line, paragraph/block, section, bounded overlapping line windows, and document-level candidates.

Phase 1A may use a simple line/window strategy. As RFC 0015 structural helpers mature, they may select complete blocks/sections/subtrees instead. The selected result still maps back to a contiguous body-line range and returns Markdown evidence.

Any chunking choice that changes passage identity belongs in retrieval-profile fingerprinting/derived-state identity, not in the agent-facing mode enum.

### 8. Compact output is normative

Default `detail="compact"` is exactly a two-column TSV with a normative header:

```text
location\tsnippet
```

Each hit occupies exactly one physical output line.

Snippet transformation is a **closed whitelist**:

1. take the complete text of every body line named by the returned range, in order;
2. replace each literal tab inside a body line with one ASCII space;
3. join adjacent selected body lines with exactly one ASCII space;
4. preserve every other character inside every body line exactly, including leading, trailing, and repeated internal spaces.

No other trimming, whitespace collapse, Unicode normalization, or textual rewriting is allowed. The renderer never cuts inside a selected body line and never omits a prefix/suffix while retaining a location that claims the whole line.

If a passage is too large for useful compact behavior, passage selection must choose a smaller **contiguous set of whole lines before rendering**. The renderer itself does not lie about provenance by truncating an already-selected line.

There is no `max_snippet_tokens` public contract in Phase 1. Token-aware packing requires a later explicit tokenizer/counting contract that preserves truthful provenance.

### 9. Score detail and score semantics

`detail="score"` is exactly:

```text
location\tscore\tsnippet
```

Scores are finite JSON numbers. Text rendering is frozen by shared conformance fixtures.

A larger score means better relevance **within the same search call**. Scores are opaque diagnostics and are not comparable across modes, profiles, or resolved engines.

### 10. Full detail has a stable semantic core and non-semantic diagnostics

`detail="full"` returns structured data. Its stable core contains request semantics and provenance-bearing results. Backend identity is not a portable retrieval semantic.

Canonical shape:

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

Normative details:

- `query` is the ASCII-trimmed query actually searched;
- `mode` is the authoritative requested mode;
- `profile` echoes the caller's requested value (`null` when absent); a resolved default profile/fingerprint belongs in diagnostics;
- `path` is the raw logical path;
- `location` uses the compact path-escaping contract;
- `body_start_line` / `body_end_line` describe the range actually returned after `context` expansion;
- `text` joins selected logical body lines with `\n` and preserves characters inside each logical line; it is not a byte-exact source slice and does not claim original line-ending spelling;
- `source_digest` identifies the authored source snapshot.

`diagnostics` is explicitly non-semantic. Implementations may add or omit backend/profile-fingerprint details for debugging and benchmark reproducibility. Portable callers must not branch on names such as `duckdb_fts`, and RFC 0002 parity does not require identical backend diagnostic keys.

### 11. Lexical mode is offline by contract

`mode="lexical"` means ranked lexical relevance over candidate passage text. It is distinct from literal substring matching. Tokenization/stemming/scoring may vary by compatible profile/engine, but those choices do not create new public modes.

The zero-configuration contract is stronger than the preferred implementation:

1. a deterministic built-in lexical scorer is sufficient to make lexical search work;
2. search may use DuckDB `fts` only when it is already locally loadable without installation/download;
3. search must not issue implicit `INSTALL fts`;
4. search must not trigger DuckDB extension autoinstall/download merely by searching;
5. search must not require network access, embeddings, or a persistent sidecar;
6. if local FTS cannot be used safely, search uses the built-in scorer rather than failing or downgrading to literal.

The built-in scorer is the cross-runtime baseline. Its formula and ordering are frozen by Phase 1 conformance fixtures when implemented.

DuckDB FTS/BM25 is Phase 1B: an optional optimization after useful Phase 1A search exists. If used, search owns any ephemeral refresh/invalidation and must not serve a stale index as current bundle state.

Persistent index build/update is a separate capability with its own effect contract; it is not an implicit side effect of `search`.

### 12. Literal mode has exact semantics

`mode="literal"` means literal substring matching over candidate logical passage text before compact rendering. It is not regex and performs no stemming, token expansion, accent folding, fuzzy matching, or extra Unicode normalization.

Case-insensitivity uses Unicode Default Case Folding on both query and candidate. Shared multilingual fixtures freeze observable behavior so Python and TypeScript cannot substitute divergent runtime-native lowercase behavior.

Every matching literal final range has score `1`, so literal ordering is the structural tie-break in section 13. `detail="score"` remains valid.

Regex, if ever added, receives a new explicit semantic rather than changing `literal`.

### 13. Ranking, fusion, deduplication, and tie-breaking

Each mode/profile first completes the scoring or fusion policy that defines its **final hit score**. In hybrid retrieval, lexical/vector candidate fusion therefore happens before public deduplication.

Only after final scoring/fusion, duplicate candidates with the same raw:

```text
(path, body_start_line, body_end_line)
```

are coalesced, retaining the best final score for that range.

Final results order by:

```text
(score DESC, path ASC, body_start_line ASC, body_end_line ASC)
```

`path` is the raw logical path and is compared by Unicode scalar/code-point order as frozen by fixtures. No internal `passage_id` participates.

For a fixed bundle, requested mode, resolved profile, resolved engine contract, and engine availability, result order is deterministic.

Different compatible engines may produce different lexical/vector relevance scores and therefore different relevance order. Cross-runtime **ranking equality** is required only when both runtimes execute the same resolved engine/profile contract. The built-in lexical scorer provides the shared baseline. Engine-specific fixtures may additionally pin behavior when both runtimes resolve the same DuckDB/extension contract.

All compatible engines must still preserve provenance, finite higher-is-better scores when exposed, final-range deduplication, and the same structural tie-break.

### 14. `context` expands after ranking and limit

`context=N` adds up to `N` immediately preceding lines and up to `N` immediately following lines to each selected base hit, bounded by the concept body.

Operation order is normative:

1. discover bundle candidates and apply filters;
2. complete mode/profile scoring or fusion;
3. coalesce duplicate final base ranges and order them;
4. select up to `limit` base hits;
5. expand each selected hit by `context`;
6. render the expanded range.

Context does not affect score, ranking, or hit count. Returned location, body range, snippet, and full-detail `text` all describe the expanded range exactly.

Distinct hits may overlap after context expansion; search does not merge them implicitly because that would change rank identity and hit count.

### 15. Vector mode hides exact/ANN execution

`mode="vector"` means semantic vector retrieval. Exact versus approximate execution is an internal profile/backend decision.

Intent-oriented examples are acceptable:

```text
mode="vector", profile="legal-local"
mode="vector", profile="legal-fast"
mode="vector", profile="legal-large"
```

One profile may internally use exact DuckDB distance, another HNSW/VSS, and another Lance. Public examples/configuration intended for agents should not encode backend names as user retrieval intent.

HNSW parameters such as `ef_search`, `M`, and `ef_construction` belong to profile configuration. There is no public `mode="ann"`.

### 16. Embeddings are a provider protocol and derived state

Embedding generation distinguishes documents from queries:

```text
embed_documents(texts) -> vectors
embed_query(query) -> vector
```

A profile records enough metadata to reject incompatible/stale vectors, including provider/model identity, revision/fingerprint when available, dimensions, distance metric, normalization contract, chunker/version, and backend/index parameters.

Providers may be local functions/models, subprocesses, HTTP services, or hosted APIs. The core API depends on this protocol, not on a vendor SDK.

Embedding/provider/index state is derived and never becomes canonical Markdown/OKF data.

### 17. Hybrid mode is a policy behind the same tool

`mode="hybrid"` combines lexical and vector candidates while preserving the same provenance/result contract.

RRF is a baseline candidate because it combines ranks without pretending BM25 scores and vector distances share a scale. Weighted fusion or staged reranking may be profile choices.

Exact RRF `k`, weights, and candidate pool sizes remain benchmark/profile questions, not public Phase 1 arguments.

### 18. Derived-state identity and lifecycle

Retrieval state should use canonical source identity plus retrieval-profile fingerprinting for incremental refresh:

```text
same source identity + same profile -> reuse compatible derived state
changed source                      -> rebuild affected passages/index rows
removed concept                     -> remove derived rows
new concept                         -> add derived rows
```

Every choice that changes passage identity or vector meaning participates in the profile/materialization fingerprint.

No Markdown file is rewritten merely to store embeddings/index metadata.

When RFC 0015 structural spans are used, the normalized parser snapshot and corresponding digest govern structural coordinates; raw-source mapping remains RFC 0015's responsibility. Search does not invent a second span coordinate system.

If RFC 0014 is accepted, its snapshot/materialization identity replaces any provisional search-specific cache naming. There must be one derived-state lifecycle, not two.

### 19. MCP annotations are static and conservative

RFC 0008 governs. The `search` tool has one annotation set per registered server instance, covering the maximum possible effect of every profile selectable through that registered tool.

Phase 1 lexical/literal-only registration can advertise:

```text
readOnlyHint=true
destructiveHint=false
idempotentHint=true
openWorldHint=false
```

Future effects combine conservatively:

- any selectable hosted/open-world profile -> `openWorldHint=true` for the tool;
- any selectable non-idempotent behavior -> `idempotentHint=false`;
- any future legal search profile that can mutate environment state -> `readOnlyHint` reflects that maximum effect.

This RFC separately forbids `search` from implicitly installing extensions or creating/updating persistent sidecars, so ordinary search need not become mutating merely because separate index-management capabilities exist.

The selectable profile set used for annotations is fixed at tool registration. A server configuration change that changes selectable profiles must rebuild/reannounce the tool metadata before those profiles can be called. Per-call annotation mutation is invalid.

### 20. Runtime ownership

Phase 1 ownership is intentionally compatible with current `main`:

- **Python:** first implementation in a focused search module called from the existing `service.py`, with CLI/MCP adapters;
- **Bundle/parser:** consume the already loaded parsed bundle and shared body-line semantics; no second scanner/parser;
- **Rust:** no second Rust ranking engine is required for Phase 1A; RFC 0015 structural IR may increasingly own passage selection;
- **TypeScript core:** no independent search semantics; consume shared fixtures as parity lands;
- **`typescript-duckdb`:** DuckDB-specific parity belongs in the DuckDB adapter when implemented.

This makes Phase 1A implementable on current `main` while remaining convergent with the proposed unified-service RFC if it is adopted later.

### 21. Canonical errors

Examples:

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

Exact diagnostic codes/messages are frozen with shared implementation fixtures. The semantic error classes above are normative; one runtime must not silently reinterpret input that another rejects.

## Benchmark contract

For a fixed task and fixed source information, benchmark these strategies where applicable:

```text
whole Markdown corpus
whole OKF bundle
filesystem grep
OKF lexical compact
OKF vector compact
OKF hybrid compact
```

A result is eligible for efficiency comparison only if task success/correctness meets the benchmark's acceptance threshold.

Among eligible results, the primary efficiency metric is:

> total tokens actually placed in agent context until the task can be answered.

This includes tool-call arguments when they enter model-visible context, search results, follow-up retrieval responses, auxiliary navigation structures, and additional evidence sent to the model.

Secondary diagnostics include task-quality score, recall@k, MRR/equivalent rank quality, latency, index/build cost, and embedding/network cost.

Bytes, characters, file size, line count, and tokenization of an entire representation may be recorded diagnostically but are not the primary optimization target.

## Proposed implementation sequence

### Phase 1A — useful offline body-aware search

The smallest useful implementation slice is:

1. factor/reuse and fixture current body-line splitting semantics;
2. derive body/passages with one-based coordinates;
3. implement deterministic built-in offline lexical scoring;
4. implement Unicode-case-folded literal substring mode;
5. implement `concept_type`, `path_glob`, and existing exclusion filters;
6. implement compact whole-line snippet rendering and location escaping;
7. implement final-range deduplication and deterministic tie-breaking;
8. implement exact `context` expansion;
9. implement `compact`, `score`, and stable-core `full` detail;
10. implement profile/mode validation and explicit unavailable-mode errors;
11. expose one `search` capability through Python service, CLI, and MCP with static Phase 1 annotations;
12. add shared conformance fixtures for query trimming, body lines, paths, filters, multilingual literal matching, ordering, locations, rendering, and errors;
13. benchmark actual agent-context tokens subject to task success/quality.

Phase 1A has no embedding, vector, ANN, persistent-sidecar, or extension-download dependency.

### Phase 1B — optional local DuckDB FTS

After Phase 1A is useful:

1. detect whether DuckDB FTS is already locally loadable without triggering autoinstall;
2. use local BM25 when safe/useful;
3. fall back to built-in lexical scoring without changing public mode semantics;
4. own ephemeral FTS refresh/invalidation;
5. add engine-specific tests/benchmarks while keeping backend identity diagnostic-only.

Phase 1B is an optimization. It does not gate the Phase 1A public feature.

### Phase 2 — exact vectors

1. define provider/profile protocol concretely;
2. materialize passage vectors as fixed-size arrays;
3. use native distance functions for exact top-k;
4. use source/profile/materialization identity for reuse;
5. keep vector search opt-in and fail clearly when unconfigured.

### Phase 3 — hybrid

1. retrieve lexical/vector candidate sets;
2. implement deterministic measured fusion;
3. push structured filters before expensive ranking where semantics permit;
4. benchmark quality and agent-context tokens against lexical-only.

### Phase 4 — indexed acceleration / persistent materialization

1. add optional HNSW/VSS or another acceleration backend;
2. benchmark recall/latency against exact vector search;
3. evaluate Lance/compatible stores for large bundles;
4. keep every materialization rebuildable and provenance-preserving;
5. if RFC 0014 is accepted, use its lifecycle rather than a search-only cache design;
6. expose any persistent build/update operation separately from `search` with its own RFC 0008 effect contract.

### Phase 5 — TypeScript parity

1. implement the shared search contract in the appropriate TS surface;
2. keep DuckDB-specific behavior in `typescript-duckdb`;
3. run language-neutral fallback/output/filter/literal fixtures;
4. run engine-specific fixtures only when both runtimes resolve the same engine contract;
5. claim parity only for capabilities demonstrated by shared conformance.

### Later policies

Only with benchmark evidence:

- rerankers;
- tokenizer-aware packing;
- graph-neighborhood expansion;
- diversity/MMR selection;
- multiple named embedding profiles;
- query routing among existing public modes.

None requires a new agent-facing search tool.

## Non-goals

This RFC does not:

- make body-line anchors durable;
- expose raw AST/IR JSON as default agent retrieval output;
- choose a permanent embedding provider;
- choose a permanent vector database;
- freeze HNSW parameters;
- freeze RRF `k`;
- define a future tokenizer contract prematurely;
- freeze one permanent chunker;
- make embeddings/indexes canonical OKF data;
- require network access for default search;
- require DuckDB FTS merely to make lexical search useful;
- require VSS/HNSW/Lance;
- expose ANN/HNSW as public modes;
- silently reinterpret unavailable vector/hybrid as lexical;
- return full document bodies by default;
- create/update a persistent sidecar as an implicit `search` effect;
- create a second parser/scanner/service ownership boundary;
- require Rust to own Phase 1 ranking.

## Open implementation questions

These are measured implementation choices, not unresolved public semantics:

1. Which structural passage strategy gives the best task quality per returned token?
2. Should the first built-in lexical scorer rank lines, windows, structural passages, or a two-stage combination?
3. Which deterministic scorer is the best cheap cross-runtime baseline?
4. At what corpus size does exact vector scan justify ANN acceleration?
5. Which fusion policy best improves task outcomes per context token?
6. Where should named profile configuration live when Phase 2 needs it?
7. When should Rust take on additional retrieval preprocessing if profiling shows Python-side construction is material?

The Phase 1 implementation PR must choose and fixture the initial scorer/chunker; those choices do not become new public arguments.

## Acceptance criteria

RFC 0016 is ready for Phase 1A implementation when the implementation can satisfy all of the following without inventing missing public semantics:

1. one CLI/MCP `search` operation over the loaded bundle;
2. public modes exactly `lexical`, `literal`, `vector`, `hybrid`;
3. lexical/literal implemented in Phase 1A, with explicit unavailable errors for vector/hybrid rather than fallback;
4. deterministic built-in lexical search requiring no provider, sidecar, extension installation, or network access;
5. body coordinates derived from the shared one-based body-line contract;
6. explicit ephemeral-location semantics across edits;
7. normative path escaping and one-physical-line-per-hit compact output;
8. whole-line snippet fidelity using only the closed transformation in section 8;
9. exact post-rank/post-limit `context=N` behavior;
10. normative `detail="score"` column order and stable semantic core for `detail="full"`;
11. backend identity confined to non-semantic diagnostics;
12. deterministic final-range deduplication and `(score, raw path, start, end)` ordering with no internal-ID tie-break;
13. exact ASCII query trimming, exact `concept_type`, and positive `path_glob` semantics;
14. explicit unknown/incompatible profile and unavailable-mode errors with `mode` authoritative;
15. RFC 0008 static maximum-effect annotations;
16. no canonical Markdown mutation, implicit extension install/download, hosted call, or persistent index write on default search;
17. conformance fixtures covering query trimming, body lines, path escaping, compact/score/full rendering, filters/exclusions, errors, multilingual case folding, deduplication, and fallback ordering;
18. benchmark reporting task success/quality and actual agent-context tokens, with retrieval/latency metrics as secondary diagnostics;
19. convergence with RFC 0015 structural selection: when the document IR is used, it selects complete Markdown evidence rather than replacing agent context with AST JSON;
20. no separate cache/materialization lifecycle if proposed RFC 0014 becomes accepted.

Local DuckDB FTS/BM25 is an accepted Phase 1B optimization when already available without implicit install/download. It is not a blocker for useful Phase 1A search. Vector, hybrid, ANN acceleration, and persistent materializations are later extensions of the same public contract.
