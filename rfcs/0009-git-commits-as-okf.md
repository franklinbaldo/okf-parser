---
type: RFC
title: Git commits as OKF documents and queryable history
status: accepted
description: Project Git commit objects and subject-first structured messages into provenance-preserving OKF relations that can drive audit, agents and Astro projections
---

# RFC 0009: Git commits as OKF documents and queryable history

## Summary

Git commit history is an authored, content-addressed knowledge corpus. A commit already combines an immutable object envelope — tree, ordered parents, author, committer, timestamps and object identity — with a message explaining intent. Consumers that want to query, audit or publish that history should not have to invent a second parser and provenance model.

This RFC adds an explicit Git source capability to `okf-parser`. Git objects are projected into canonical OKF records and relational surfaces without pretending that commits are Markdown files in the worktree and without teaching ordinary filesystem discovery to scan `.git`.

Structured commit messages use a **subject-first OKF envelope**:

```text
Tell the repository why this change exists

--- okf
type: Change
publish: true
topics:
  - git
  - okf
---

The Markdown narrative begins here.
```

The first paragraph remains useful to ordinary Git tooling and GitHub. The optional `--- okf` block carries failsafe YAML metadata. The remainder is Markdown body.

The immediate dogfood is an Astro blog whose selected posts are commit messages. The broader capability is queryable history for agents, releases, timelines, audits, provenance and other projections.

Implementation is decomposed into #87 through #91.

## Architectural boundary

The source pipeline is:

```text
Git object database
  -> explicit Git source adapter
  -> canonical commit/message records + provenance
  -> OKF relations / TypeContract
  -> DuckDB, Ibis, agents, Astro and other projections
```

The Git object database is authoritative. GitHub REST, GraphQL and rendered HTML are not required and do not define semantics.

Ordinary `load_bundle(path)` / `loadBundle(path)` remain filesystem-only. They do not discover `.git`, checkout historical trees, rewrite history or fetch remotes implicitly.

## Shipped substrate

RFC 0009 lands after the repository's ingestion architecture changed materially. Implementations MUST reuse the current authorities rather than reproduce the older pre-Rust design.

1. **Content identity is already canonical.** Valid UTF-8 source uses the shipped `source_digest`; canonical projected OKF value uses the shipped `parsed_digest` contract.
2. **The Rust engine is now the preferred heavy path.** #103 promoted `okf-core` to end-to-end discovery/read, YAML/frontmatter, Markdown facts, validation/link resolution, digests and direct DuckDB materialization. Git-specific object walking, commit envelope semantics, provenance and ref policy remain source-adapter concerns, but implementations SHOULD reuse the Rust engine wherever the existing stable boundary can consume the decoded/projected document without duplicating semantics.
3. **Capability-driven ingestion remains normative.** Identity-only/object-envelope queries must not force message decoding, YAML, Markdown facts or body retention when those facts were not requested. Work remains bounded, deterministic and cancellable.
4. **Relational/type contracts are reusable.** Git history reuses TypeContract and normalized DuckDB/Ibis semantics; it does not require the filesystem-specific session object to own Git history.
5. **Git history is read-only in the first milestone.** Filesystem edit/apply/import capabilities do not imply commit amendment, ref mutation or note writes.

## Decision

### 1. Every reachable commit is representable

A commit remains representable even when its message contains no authored OKF block. This is necessary for existing repositories, hosting-generated merges and gradual adoption.

A plain message projects with:

- `type: Commit`, derived by adapter policy;
- `title`, derived from the Git subject;
- body from the ordinary message body;
- empty authored extension metadata;
- explicit provenance distinguishing derived policy from authored metadata.

A repository may require structured envelopes prospectively through validation policy. Reading old history never requires rewriting it.

### 2. Subject-first envelope grammar

The v1 logical grammar is:

```text
SUBJECT LF
LF
[ "--- okf" LF
  YAML LF
  "---" LF
  LF ]
BODY
```

Normative rules:

- `SUBJECT` is the first paragraph and is non-empty under the strict authoring profile;
- the optional opener is exactly `--- okf` on the first line after the blank line following the subject;
- the closing delimiter is exactly `---`;
- YAML uses the same failsafe OKF value family: `null | string | array | object`;
- custom executable tags and unsupported YAML values are invalid;
- the body may be empty;
- without an envelope, all content after the subject is ordinary body;
- once the exact opener is present, a missing/invalid closer is a malformed envelope, not a plausible plain message;
- a second envelope is invalid;
- newline/BOM behavior is pinned by language-neutral conformance vectors;
- Conventional Commit subjects are ordinary subjects; Conventional Commits are not core OKF semantics.

### 3. The subject is the canonical title

The Git subject projects to canonical `title`. The authored YAML block MUST NOT contain another `title` in v1.

The following namespaces are adapter-owned and cannot be overridden by authored metadata:

- `title`;
- commit/tree/parent object identities;
- author/committer identities and timestamps;
- repository/hash algorithm;
- ref reachability;
- raw message/source digests;
- diagnostics and provenance.

An authored attempt to claim an adapter-owned key is a structural diagnostic.

### 4. Effective semantic type is distinct from source kind

The source record always has `source_kind = git_commit`.

The effective OKF `type` comes from authored metadata when present and otherwise derives to `Commit`. Producer types such as `Change`, `Decision`, `Release` or `Post` are allowed without losing the fact that the source is a commit.

### 5. `parsed_digest` hashes the effective canonical OKF projection

This point is normative and resolves the final ambiguity in the earlier proposal.

For a valid UTF-8 Git message, `parsed_digest` is computed over the same canonical `[frontmatter, body]` value used by the shipped digest contract, where the **frontmatter value is the effective semantic projection**:

- canonical `title` derived from the subject is included;
- effective `type` is included, including policy-derived `type: Commit` when no authored type is present;
- authored extension metadata is included after reserved-key validation;
- Markdown body is included under the shipped newline normalization rules.

Git-object facts are NOT included in that `parsed_digest`: commit/tree/parent OIDs, author/committer envelope data, repository identity, ref observations, signatures and provenance remain separate Git facts. The qualified commit OID already binds the whole commit object, while `source_digest` identifies the exact message source.

Therefore two messages that project to different effective semantic title/type values cannot accidentally share a `parsed_digest`, while changes only to ref reachability or other external Git observations do not alter the semantic message digest.

A message that is not valid UTF-8 remains representable through Git object facts/OID but has an incomplete message projection. The adapter MUST NOT replacement-decode it or invent `source_digest`/`parsed_digest` values for a decoded fiction.

### 6. Authored and Git-derived facts retain provenance

At minimum, consumers can distinguish:

| Fact | Authority |
| --- | --- |
| subject/title and Markdown body | authored commit message |
| metadata inside `--- okf` | authored commit message |
| default `type: Commit` | adapter policy |
| commit, tree and ordered parent OIDs | Git object |
| author/committer identities and times | Git object |
| selected-ref reachability | repository/ref observation |
| source/parsed digests | deterministic parser derivation |
| Git Notes | selected notes ref and note blob |

Convenience JSON may combine these fields, but relational and diagnostic surfaces preserve their authority boundary.

### 7. Object identity is hash-algorithm-qualified

A bare hex OID is insufficient as a cross-repository identity. Records carry at least:

```text
repository_identity
object_format  # sha1 or sha256
object_type    # commit, tree, blob, tag
oid
```

Repository identity is an explicit input/observation, not permanently inferred from one filesystem path.

### 8. Parents are ordered and repeated

A commit has zero or more ordered parent headers. The adapter preserves `parent_index`; root commits have no parent rows; octopus merges are ordinary. Parent edges are Git facts and cannot be authored in the message.

### 9. Refs are observations, not identity

Branches and tags move. Selected refs and their reachability are recorded separately from commit identity. A caller may select exact OIDs, refs or ranges; defaults must be explicit in the public API/result.

### 10. Incomplete history is explicit

Shallow clones, partial clones and missing objects MUST NOT appear as falsely complete history. Completeness and missing-parent/object diagnostics are machine-readable. Consumers decide whether incomplete history is acceptable for their projection.

### 11. Ingestion is bounded, deterministic, cancellable and projection-aware

The adapter must not create one task/promise per commit or retain every body merely because every object is reachable.

Required properties:

- bounded object-read admission;
- bounded parsing admission;
- backpressure between walk/read/parse/emission;
- deterministic records and diagnostics independent of concurrency;
- cancellation stops admission of new work;
- metadata-only projections avoid body/YAML/Markdown work where possible;
- filters and selected projections are pushed toward the earliest safe stage;
- stable explicit ordering is used rather than incidental completion order.

A future million-commit corpus must not imply a million simultaneous tasks, open descriptors or retained message bodies.

### 12. Normalized query surfaces

The first relational milestone exposes the equivalent of:

- `commits` — one row per observed commit object;
- `commit_parents` — ordered parent edges;
- `commit_refs` — selected-ref/reachability observations;
- `commit_metadata` — authored extension metadata not projected into a declared typed relation;
- `git_diagnostics` — malformed messages, incomplete history and object errors.

Declared authored types compile through existing TypeContract and DuckDB/Ibis semantics. Bodies/raw messages may be optional/lazy columns so metadata-only queries do not force full materialization.

### 13. Commit documents and file Revisions are many-to-many

RFC 0009 does not collapse file Revision provenance from #53 into commit identity.

```text
one commit observes zero or more file Revisions
one file Revision may be observed in many commits
```

Renames, merges, cherry-picks and unchanged content remain representable without assigning one artificial “owner commit” to a Revision.

### 14. Git Notes are separate overlay documents

Git Notes are useful for agent handoffs, evaluations and annotations that should not amend the canonical commit message. They are separate records with their own notes ref, target object, note blob identity, parsed OKF value, provenance and diagnostics.

Notes are opt-in because clones do not necessarily fetch all notes refs. Results distinguish “selected namespace available with no note” from “namespace unavailable/unfetched”. Notes never silently replace commit metadata or body.

Reading overlays is #90. Writing notes requires a separate mutation contract.

### 15. Prospective repository policy

A validation surface may require structured messages for new commits without changing how legacy history is read.

The first policy surface supports:

- validation of one commit-message file for `commit-msg` hooks;
- validation of a range for CI/pre-receive use;
- optional envelope requirement for non-merge commits;
- optional type requirements;
- distinct diagnostics for absent versus malformed envelope;
- no implicit rewrite/amend.

Merge commits require explicit host/project policy rather than an assumption that hosting-generated merge messages contain an envelope.

### 16. Astro/blog publication is a projection

#91 provides a TypeScript/Astro reference adapter using the official in-process API.

Publication is explicit (`publish: true`, a producer-defined type, or another explicit predicate). Core never publishes all commits by default.

The projection derives:

- title from subject;
- body from canonical message body;
- date from an explicit author/committer-time policy;
- tags/language/description/slug from authored metadata;
- immutable source identity from the qualified commit OID.

Slug collisions are deterministic failures or use an explicitly documented deterministic disambiguation rule. Empty wrapper Markdown files are not an architectural requirement.

### 17. Python, TypeScript and Rust observable semantics converge

The envelope grammar, effective projection, digests, identity qualification, parent ordering, provenance classes and diagnostics are language-neutral.

Python and TypeScript expose idiomatic APIs but MUST pass the same conformance vectors. Rust may own shared parsing/execution primitives when the stable engine boundary applies, but no runtime may introduce a different Git-message semantic contract.

## Diagnostics

Implementation assigns stable Git-specific diagnostic codes covering at least:

- absent envelope when policy requires it;
- malformed opener/closer/YAML;
- duplicate envelope;
- reserved adapter-owned key;
- empty/invalid subject under strict policy;
- invalid UTF-8 message projection;
- unsupported object format/type;
- missing parent/object;
- shallow/incomplete history;
- unavailable selected ref or notes namespace.

One malformed message does not abort unrelated discovery unless fail-fast policy is explicitly selected.

## Security and trust

Commit messages and notes are untrusted data. YAML stays within OKF's safe value model and Markdown is never executed by the parser.

The adapter does not:

- checkout historical trees;
- execute hooks while reading history;
- execute message content;
- amend commits;
- rewrite/move refs;
- fetch remotes implicitly;
- trust signatures merely because signature headers exist.

If the `git` executable is used, arguments must not rely on shell interpolation and parse semantics must not depend on ambient user configuration.

## Rejected alternatives

### Leading YAML frontmatter in raw commit messages

Rejected because ordinary Git/GitHub subject views would become `---`.

### Git trailers as the sole structured syntax

Rejected for v1 because nested OKF values and an explicit Markdown boundary fit the subject-first envelope better. Selected trailers may later become derived metadata with separate provenance.

### Store canonical content only in Git Notes

Rejected because notes are independently mutable and often unfetched. They are overlays, not the canonical authored commit narrative.

### Generate one wrapper Markdown file per commit

Rejected as an architectural requirement. It creates duplicate identity and content state.

### Make every commit public

Rejected. Publication is an explicit producer projection.

### Query GitHub APIs as core history

Rejected. The local Git object database remains the authority and works offline/private.

### Rewrite historical commits during adoption

Rejected because it changes OIDs, signatures and shared history. Policy applies prospectively.

## Implementation sequence

1. **#87 — message contract**
   - shared envelope/digest conformance vectors;
   - Python + TypeScript parse/format/validate APIs;
   - prospective `commit-msg` validation surface;
   - idempotent formatting without history rewrite.

2. **#88 — object index**
   - selected refs/OIDs/ranges;
   - bounded Git object admission;
   - commits, ordered parents, refs and completeness diagnostics;
   - projection pushdown and cancellation.

3. **#89 — relational/query surfaces**
   - normalized DuckDB/Ibis surfaces;
   - authored TypeContract projections;
   - deterministic JSON-ready adapter projection;
   - lazy/selected raw body/message data.

4. **#90 — Notes overlays**
   - explicit notes namespaces;
   - separate note records and joins;
   - fetch/push refspec documentation.

5. **#91 — Astro dogfood**
   - in-process TypeScript virtual content loader;
   - explicit publication policy;
   - deterministic dates/slugs/routes/feed behavior.

## Acceptance

RFC 0009 is accepted with these decisions:

- Git is an explicit source capability, not filesystem Markdown;
- subject-first `--- okf` is the v1 authoring grammar;
- plain/legacy commits remain representable;
- subject is canonical title;
- effective semantic type is distinct from `source_kind = git_commit`;
- `parsed_digest` hashes effective canonical semantic `title`/`type` + authored extensions + body, excluding Git object/ref/provenance facts;
- Git-derived and authored facts retain distinct provenance;
- identities are object-format-qualified;
- multi-parent merges and movable refs are first-class;
- incomplete history is explicit;
- ingestion is bounded, deterministic, cancellable and projection-aware;
- current Rust engine capabilities are reused rather than reimplementing a third semantics;
- Git Notes are separate opt-in overlays;
- commit documents and file Revisions compose many-to-many;
- Astro publication is explicit and outside core;
- Python/TypeScript/Rust observable semantics share conformance vectors;
- no GitHub service, checkout, history rewrite or wrapper files are required.

Implementation completion remains separate from RFC acceptance.
