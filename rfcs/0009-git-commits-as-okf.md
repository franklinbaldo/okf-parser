---
type: RFC
title: Git commits as OKF documents and queryable history
status: proposed
description: Project Git commit objects and subject-first structured messages into provenance-preserving OKF relations that can drive audit, agents and an Astro blog
---

# RFC 0009: Git commits as OKF documents and queryable history

## Summary

A Git commit already combines two useful kinds of knowledge:

1. an immutable object envelope with a tree, ordered parents, author, committer,
   timestamps and object identity; and
2. an authored message that explains intent and may contain a longer narrative.

Those facts are usually consumed only as prose in `git log`, even though they
form a historical knowledge graph. Every project that wants to query commits,
connect them to concepts, or publish selected messages currently invents a
parallel parser and provenance model.

This RFC defines an optional Git source adapter for `okf-parser`. The adapter
projects reachable commit objects into canonical OKF concepts and normalized
relations without pretending that commits are Markdown files in the worktree.

The raw message uses a **subject-first OKF envelope**:

```text
Tell the repository why this change exists

--- okf
type: Change
publish: true
slug: repository-tells-its-story
topics:
  - git
  - okf
---

The Markdown narrative begins here. It can be a release note, an audit record,
an agent handoff, or the complete source of a blog post.
```

The first paragraph remains an ordinary useful Git subject. An exact
`--- okf` block immediately after it carries failsafe YAML metadata. The
remainder is Markdown body.

This is deliberately an adapter grammar rather than a relaxation of strict
filesystem OKF. Leading YAML frontmatter in a commit message would make the
subject of every commit `---` in `git log --oneline` and GitHub. A
subject-first envelope preserves Git interoperability and projects
deterministically into the same canonical OKF value model.

The immediate dogfood is an Astro blog whose selected posts are commit
messages. The broader capability is a queryable repository history for agents,
audits, releases, timelines, topic graphs and future projections.

Issue #86 tracks the architectural decision. Implementation is decomposed into
#87 through #91.

## Motivation

### A commit is already a historical document

The commit object is not merely a pointer to a diff. Its object identity binds
the complete commit payload, including:

- the exact tree;
- zero or more ordered parents;
- author identity and authored time;
- committer identity and committed time;
- optional signatures and extra headers;
- the exact message bytes.

That makes a commit suitable as an immutable source document. The message can
carry authored semantics; the object envelope supplies provenance and graph
structure that a Markdown file would otherwise need to duplicate.

### Commit messages are underused authored content

Good commit messages already explain:

- what changed;
- why it changed;
- what alternatives were rejected;
- which constraints matter;
- how a migration or release should be understood.

A longer message is often close to a small article. Storing a second blog post,
release note or agent handoff with the same narrative introduces drift. If the
commit message is the authored document, the same source can produce multiple
views without duplication.

### Git history is a graph, not a folder

Filesystem OKF discovery starts from Markdown paths. Git history starts from
refs and walks a directed acyclic graph of content-addressed objects. Teaching
the strict Markdown walker to scan `.git` would collapse two source models
and make checkout layout accidentally authoritative.

The existing architecture already supplies the correct boundary:

```text
Git object database
  -> Git source adapter
  -> canonical records + explicit provenance
  -> OKF relations / TypeContract
  -> DuckDB, Ibis, agents, Astro and other projections
```

### The blog is a projection, not the architecture

An Astro site is a useful proof because it exercises title, body, date, tags,
language, slug, publication policy and stable identity. But `okf-parser`
should not own routes, feeds, HTML or one blog taxonomy.

The core result is a queryable commit corpus. Astro is one consumer.

## Decision

### 1. Git is an optional source capability

Ordinary `load_bundle(path)` and strict Markdown validation remain
filesystem-only. They do not discover `.git`, shell out implicitly, or change
the meaning of a conformant OKF bundle.

Git history is loaded through an explicit capability/API. Illustrative names
are non-normative:

```text
load_git_history(repository, refs=[...], projection=[...])
bundle.with_git_history(...)
```

The public shape may be refined in #88 and #89, but invoking the Git adapter is
always explicit.

The object database is authoritative. GitHub REST, GraphQL and rendered HTML
are not required and do not define semantics.

### 2. Every reachable commit is representable

The adapter represents a commit even when its message has no authored OKF
block. This is necessary for existing repositories, merge commits produced by
hosting platforms and gradual adoption.

A plain message projects with:

- `type: Commit`, derived by adapter policy;
- `title`, derived from the Git subject;
- body from the ordinary message body;
- empty authored extension metadata;
- explicit provenance marking `type` and `title` as derived rather than
  authored frontmatter.

A repository that wants every new non-merge commit to contain explicit OKF
metadata enables the validation policy described below. Reading old history
never requires rewriting it.

### 3. Authored messages use a subject-first envelope

The v1 grammar is:

```text
SUBJECT LF
LF
[ "--- okf" LF
  YAML LF
  "---" LF
  LF ]
BODY
```

Rules:

- `SUBJECT` is the first paragraph and must be non-empty for the strict
  authoring profile;
- the optional opener is exactly `--- okf` on the first line after the blank
  line following the subject;
- the closing delimiter is exactly `---`;
- the metadata block uses the same failsafe YAML value family accepted by
  strict OKF frontmatter: null, strings, arrays and objects;
- aliases, custom tags and producer-specific executable YAML features remain
  invalid;
- the body after the block is Markdown and may be empty;
- with no envelope, the entire content after the subject is the ordinary body;
- once the exact opener appears, a missing/invalid closing delimiter is a
  malformed envelope, not a plausible plain message;
- more than one envelope is invalid;
- newline/BOM normalization follows shared language-neutral vectors and never
  depends on platform locale.

The opener includes `okf` so an ordinary Markdown horizontal rule in a
conventional commit body cannot be mistaken for metadata.

The parser returns the exact raw message identity in addition to normalized
values. Formatting is opt-in and idempotent; parsing never rewrites a commit.

### 4. The subject is the canonical title

The Git subject projects to the canonical concept `title`. The YAML block
does not accept another `title` in v1.

This avoids two authored titles that can disagree and preserves the strongest
ordinary Git affordance: the first line says what the document is about. A blog
post that needs a title should use that title as the commit subject.

The following keys are adapter-owned and cannot be overridden in the authored
block:

- `title`;
- commit/tree/parent object identities;
- author/committer identities and timestamps;
- repository/hash algorithm;
- ref reachability;
- raw-message/source digests;
- diagnostics and provenance.

An authored attempt to use a reserved adapter key is a structural diagnostic.
It is never silently preferred or ignored.

### 5. Authored `type` is semantic; source kind remains Git commit

The source record always has `source_kind = git_commit`. The effective OKF
`type` comes from the authored block when present and otherwise derives to
`Commit`.

Examples include `Change`, `Decision`, `Release`, `Post` and
producer-specific types. Core does not prescribe that taxonomy.

Separating source kind from semantic type matters:

- a `Post` remains provably sourced from a commit;
- queries can select all commit objects regardless of authored type;
- type-specific contracts can validate the authored metadata;
- the adapter need not inject a fake `GitCommit` domain type into every
  producer taxonomy.

### 6. Git-derived and authored facts retain provenance

The canonical projection does not flatten everything into an indistinguishable
map.

At minimum, consumers can distinguish:

| Fact | Authority |
| --- | --- |
| subject/title and Markdown body | authored commit message |
| metadata inside `--- okf` | authored commit message |
| default `type: Commit` | adapter policy |
| commit, tree and parent OIDs | Git object |
| author/committer identities and times | Git object |
| ref reachability | selected repository/ref observation |
| parsed/source digests | deterministic parser derivation |
| Git Notes content | selected notes ref and note blob |

A user-facing JSON projection may combine these for convenience, but relational
and diagnostic surfaces retain the authority boundary.

Git object headers win over authored metadata because the authored block is not
allowed to claim those names.

### 7. Object identity is hash-algorithm-qualified

A bare hexadecimal OID is insufficient as a cross-repository identifier.

Records qualify identity with at least:

```text
repository_identity
object_format  # sha1 or sha256
object_type    # commit, tree, blob, tag
oid
```

A canonical serialized identifier may be derived from those fields, but the
record does not assume every Git repository uses SHA-1.

Repository identity is an explicit input/observation, not inferred permanently
from one filesystem path. Clones may move; two unrelated repositories may
contain identical objects.

### 8. Parents are ordered, repeated relations

A commit has zero, one or multiple ordered parent headers. The adapter never
flattens them into one scalar or assumes two-parent merges.

The normalized relation includes `parent_index`, preserving header order.
Root commits have no parent rows. Octopus merges are ordinary.

The commit graph edge is derived from Git headers and cannot be authored in the
message block.

### 9. Refs are observations, not commit identity

Branches and tags can move. A commit does not own one branch, and a branch name
is not stable object identity.

The adapter records selected-ref reachability separately from commit records,
including the observed ref target when available. Repeated indexing may observe
new ref state without changing old commit object facts.

A caller may select exact OIDs, refs or ranges. Defaults must be explicit in the
public API and results.

### 10. Shallow and incomplete history is explicit

A shallow clone, partial clone or missing object must never yield a falsely
complete graph.

The result records completeness and diagnostics, including missing parent/object
identity when known. Consumers can choose whether incomplete history is allowed
for their projection.

A static blog may accept a deliberately selected range. An audit claiming full
repository history should fail closed when the source is incomplete.

### 11. The Git adapter is bounded and pushdown-aware

The design target includes very large histories. It must not create one
promise/task or retain every message body merely because every reachable commit
exists.

The capability contract is:

- bounded object-read concurrency;
- bounded parsing concurrency;
- backpressure between walk, read, parse and emission;
- deterministic output independent of concurrency;
- cancellation stops admission of new work;
- selected columns/projections avoid message-body parsing or retention where
  possible;
- ref/range and metadata filters are applied as early as their required facts
  allow;
- stable explicit ordering rather than incidental completion order.

Implementations may use native Git libraries, the `git` executable, or a
combination, but the observable records and limits remain shared.

### 12. Normalized relational surfaces are first-class

The exact physical schema is finalized in #89, but v1 exposes the equivalent of:

- `commits` — one row per observed commit object;
- `commit_parents` — ordered parent edges;
- `commit_refs` — reachability/selected-ref observations;
- `commit_metadata` — authored extension fields that do not fit a declared
  typed relation;
- `git_diagnostics` — malformed messages, incomplete history and object errors.

Declared authored types can compile through the existing TypeContract and typed
DuckDB/Ibis path. The Git envelope does not introduce a second domain schema
language.

Bodies and raw messages may be lazy/optional columns so metadata-only queries do
not force full materialization.

### 13. Commit documents and file Revisions are many-to-many

Issue #53 defines exact source/parsed identities and optional historical
Revision provenance for files. This RFC does not replace that work.

The relationship is:

```text
one commit observes zero or more file Revisions
one file Revision may be observed in many commits
```

A Revision is not assigned to exactly one introducing commit merely because one
walk encountered it first. Renames, merges, cherry-picks and identical blobs
remain representable.

The commit message is its own source document. File contents at the commit are
other source documents connected through explicit provenance.

### 14. Git Notes are separate overlay documents

Git Notes are valuable for agent handoffs, evaluations, review state and
annotations that should not amend the canonical commit message. They live in
separate refs and can evolve independently.

That independence is exactly why notes cannot be silently merged into authored
commit metadata.

When explicitly enabled, a note is a separate record containing:

- notes ref namespace;
- target object identity;
- note blob identity;
- exact and parsed content identity;
- parsed OKF metadata/body;
- provenance and diagnostics.

Consumers join notes to commits by explicit policy. Multiple namespaces remain
separate.

Notes are opt-in because normal clone/fetch configuration does not necessarily
transport them. Results distinguish “namespace fetched and no note” from
“namespace not available”.

Writing notes is outside the first read-side milestone and is tracked in #90.

### 15. Repository policy may require envelopes for new commits

The parser remains able to ingest legacy/plain messages. A separate validation
mode enforces authored structure for repositories that choose it.

The first policy surface should support:

- validate one commit-message file for `commit-msg` hooks;
- validate a range for CI/pre-receive use;
- optionally require an envelope for non-merge commits;
- optionally require declared type specifications;
- report malformed/absent envelope separately;
- never rewrite a commit or amend history implicitly.

The hook delegates to the same parser as API/CLI. It is distribution guidance,
not a second parser embedded in shell.

Merge commits require an explicit policy because hosting platforms often create
them. A project may accept a derived `Commit` for merges while requiring
authored envelopes elsewhere, or require a merge-message template it controls.

### 16. The Astro blog is a virtual projection

Issue #91 supplies a reference TypeScript/Astro adapter. It consumes the
official `okf-parser` TypeScript API in-process during a static build.

Publication is explicit. A recommended producer contract may use
`publish: true` or an authored `Post` type, but core does not make every
commit public.

The adapter derives:

- title from subject;
- body from the message Markdown body;
- dates from an explicit producer policy over author/committer time;
- tags/language/description/slug from authored metadata;
- immutable source identity from the qualified commit OID.

Slug conflicts are deterministic hard failures or use an explicitly documented
disambiguation policy. Unpublished commits never enter routes, feeds or
sitemaps.

The preferred result is a virtual content collection or loader. Empty wrapper
files are not generated merely to satisfy an imagined Astro limitation.

### 17. Python and TypeScript share the contract

The message grammar, identity qualification, parent ordering, provenance
classes, diagnostics and conformance fixtures are language-neutral.

One runtime may implement a capability first, but observable records must not
drift. TypeScript availability matters for the Astro dogfood and should follow
the same bounded-I/O architecture rather than a separate all-at-once loader.

### 18. No external service is required

A caller can inspect or publish a local repository in-process. GitHub, MCP,
GraphQL and HTTP may expose the capability later, but none defines it.

The capability may be surfaced through existing adapters when useful.
Transport-specific schemas remain thin views over the same service/core
records.

## Authoring examples

### Plain legacy commit

```text
Fix cancellation admission after an in-flight read

Do not schedule replacement I/O after the abort signal becomes observable.
```

Projection:

```yaml
type: Commit        # derived
title: Fix cancellation admission after an in-flight read  # authored subject
```

The body is authored. The absence of an envelope is observable.

### Structured change

```text
Bound ordered Git object reads

--- okf
type: Change
topics:
  - git
  - performance
issue: "#88"
---

Walk refs lazily, keep a bounded read window, and emit in deterministic graph
order.
```

### Commit as blog post

```text
The repository begins to tell its own story

--- okf
type: Post
publish: true
slug: repository-tells-its-story
lang: en
tags:
  - git
  - knowledge
---

A commit message can be more than an explanation attached to a diff...
```

No second Markdown post file is authored.

### Agent annotation through Git Notes

The commit remains unchanged. A note in
`refs/notes/okf/agent-handoffs` may contain a strict OKF document such as an
`Evaluation` linked to the target commit. The note is queried as a separate
source and never becomes the post body implicitly.

## Diagnostics

Diagnostic codes should be assigned in implementation without overloading
filesystem OKF codes. Required categories include:

- absent envelope when policy requires one;
- malformed opener/closer or YAML;
- duplicate envelope;
- reserved adapter-owned key;
- empty/invalid subject under strict policy;
- unsupported object format/type;
- missing parent/object;
- shallow/incomplete history;
- unavailable selected ref or notes namespace;
- nondeterministic/ambiguous slug policy at the Astro layer.

Parsing one malformed message should not abort unrelated commit discovery unless
the caller selected fail-fast policy.

## Security and trust

Commit messages and notes are untrusted content. YAML remains failsafe and
Markdown is data; renderers own HTML sanitization.

The adapter reads Git objects. It does not:

- checkout historical trees;
- execute hooks;
- execute message content;
- rewrite refs;
- amend commits;
- fetch from a remote implicitly;
- trust a signature merely because a signature header exists.

Signature extraction and signature verification are separate capabilities.
Verification policy is outside v1.

Using the `git` executable, if chosen, must avoid shell interpolation and
environment-dependent configuration that changes parse semantics.

## Rejected alternatives

### Require leading YAML frontmatter in the raw commit message

Rejected. The first line would be `---`, so ordinary Git and GitHub subject
views would lose the useful title of every commit.

### Put structured metadata only in Git trailers

Rejected as the canonical v1 syntax. Trailers are good for flat repeated keys
and Git tooling, but nested OKF values and a visible Markdown document boundary
fit the explicit `--- okf` envelope better. A future adapter may project
selected trailers as derived metadata with their own provenance.

### Store the post only in Git Notes

Rejected. Notes are not part of commit object identity, may be updated
independently and are not fetched by default. They are excellent overlays, not
the canonical authored commit narrative.

### Generate one empty Markdown file per commit

Rejected as an architectural requirement. It creates a second filesystem index
and duplicates identity without adding authored content. An adapter may emit
derived build artifacts only if a real host API requires them.

### Make every commit public by default

Rejected. Repository history may contain chores, fixups, merge messages,
operational detail or content never intended for publication. Publication is an
explicit projection predicate.

### Treat a file Revision as belonging to one commit

Rejected. Unchanged content can be observed in many commits and histories can
merge or rename it. This RFC composes with #53 through many-to-many provenance.

### Parse Git history through GitHub APIs

Rejected as core architecture. It would make network/provider state
authoritative and would not work for local/private/offline repositories without
another semantics.

### Teach strict Markdown discovery to walk `.git`

Rejected. Git objects are not worktree Markdown files. The source-adapter
boundary keeps both models honest.

### Require rewriting historical commits during adoption

Rejected. It changes object identities, signatures and shared history. Legacy
messages remain representable; policy applies prospectively.

## Compatibility

This RFC changes no existing filesystem parsing, Bundle semantics, CLI command
or MCP tool.

The Git capability is optional and explicit. Existing repositories can be
indexed without changing history. A commit hook/policy is opt-in.

Authored extension metadata preserves unknown fields under existing OKF
extension principles, subject to the small reserved-key set required to protect
Git facts.

## Implementation plan

1. **#87 — message contract**
   - publish language-neutral envelope fixtures;
   - implement pure parse/format/validate functions in Python and TypeScript;
   - add one-message and range validation surfaces;
   - document hook integration.

2. **#88 — object index**
   - walk selected refs with bounded admission;
   - parse commit objects, ordered parents and messages;
   - qualify identities and report incomplete history;
   - expose cancellation and projection selection.

3. **#89 — relational/query surfaces**
   - expose normalized Bundle/Ibis/DuckDB relations;
   - compile authored types through TypeContract;
   - preserve lazy/selected bodies and deterministic ordering;
   - add JSON-ready adapter projection.

4. **#90 — Notes overlay**
   - read explicitly selected notes namespaces;
   - index note blobs as separate OKF sources;
   - expose explicit joins and fetch/push guidance.

5. **#91 — Astro dogfood**
   - implement a TypeScript virtual content loader;
   - publish only explicitly selected commits;
   - test routes, feeds, dates and slug conflicts deterministically.

6. **Composition with #53**
   - join commit observations to file Revision provenance without creating a
     1:1 ownership fiction.

## Acceptance criteria

The RFC can move from `proposed` to `accepted` when the project agrees that:

- commits are an explicit Git source capability, not filesystem Markdown;
- the subject-first envelope is the authored v1 grammar;
- plain/legacy commits remain representable;
- repositories may require envelopes prospectively through policy;
- the subject is the canonical title;
- Git object facts and authored metadata retain separate provenance;
- identities are object-format-qualified;
- ordered multi-parent merges and movable refs are first-class;
- incomplete history is explicit;
- traversal is bounded, deterministic, cancellable and projection-aware;
- Git Notes are separate opt-in overlay documents;
- commit documents and file Revisions compose many-to-many;
- Astro/blog publication is explicit and remains outside core;
- Python and TypeScript share conformance records;
- no GitHub service, checkout, history rewrite or empty post files are required.

Implementation completion remains separate from RFC acceptance.
