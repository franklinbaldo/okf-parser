---
type: RFC
title: The fact profile
status: draft
description: A conservative superset of OKF v0.2 naming its atom a fact and its scope a context, with self-describing type specifications, stable fact identity, no reserved filenames, and a declared adapter boundary for foreign specifications
---

# RFC 0017: The fact profile

## Summary

OKF v0.2 is a good floor and a poor ceiling. It requires almost nothing of a
concept — a non-empty `type` — and then spends much of its strictness on the two
documents that carry the least structured information, `index.md` and `log.md`.
This RFC proposes `fact`: a profile that keeps OKF input readable, moves useful
structure into ordinary facts, and lets a context describe itself instead of
depending on flags its consumers must remember to pass.

`fact` is **opinionated in what it emits and permissive in what it accepts**.
A plain OKF `type: Procedure` remains valid input. The preferred `fact` form is
more informative: the `type` value can itself reference the specification that
defines the type, normally inside the context, for example
`.fact/specs/Procedure.md`.

The transport or publication scheme is not the point. An absolute URI — including
an HTTP(S) URL — is useful when a specification is published externally, but it
is not more canonical merely because it has a protocol. A self-contained context
should not need the network to explain its own types.

## Scope: the profile is provider-local

Everything this RFC decides applies **inside a native `fact` context** — a
directory carrying a `.fact/` marker (decision 3). Nothing here changes what
plain OKF means for a directory without that marker.

That boundary is not a courtesy to reviewers; it is what makes the proposal
implementable beside the rest of the RFC train:

| concern | plain OKF / markerless directory | native `.fact/` context |
| --- | --- | --- |
| identity | `concept_id` derived from the bundle-relative path (RFC 0012) | adds a stable `fact_id` that survives a move |
| rename | `removed` + `added`, even at equal `parsed_digest` | preserved identity, possibly broken references |
| `index.md` / `log.md` | reserved by filename, validated as today | ordinary facts; `index`/`log` are types |
| vocabulary | `bundle`, `concept` | `context`, `fact` |
| `type` | any non-empty string | same, with a specification reference preferred |

Reading the left column is not optional for a `fact` implementation. A
markerless directory encountered by `fact` tooling is a **compatibility view**
and keeps base semantics; the profile's rules switch on only where a context
declares itself.

Retiring the reserved filenames and renaming `bundle`/`concept` are therefore
**profile-local decisions**, not proposals to change the base format. Whether
any of them should ever migrate into base OKF is a separate question that needs
its own RFC, its own migration, and evidence from running `fact` contexts.

## Relationship to RFC 0012

RFC 0012 pins identity for the shipped filesystem provider: `concept_id` comes
from the bundle-relative path, `logical_key` is currently the same value, and
renaming `notes/a.md` to `notes/b.md` is removed + added even when
`parsed_digest` is identical. Equal digests may at most produce an advisory
`possible_move`.

Decision 6 of this RFC wants the opposite property, and the two are compatible
only because RFC 0012 already says how:

> A future source adapter may define another canonical identity under its own
> RFC. `diff` consumes the provider's canonical `concept_id`; it never guesses
> identity from title, body, or digest.

This RFC is that adapter's RFC. The reconciliation is therefore explicit:

1. **The `.fact/` provider is a distinct provider**, not a reinterpretation of
   the filesystem provider. It supplies its own canonical identity, sourced from
   the authored stable id rather than from the path.
2. **The filesystem provider is unchanged.** For a markerless directory,
   RFC 0012's identity and rename semantics hold exactly as written, including
   `removed` + `added` at equal digest.
3. **Identity still comes from the provider, never from content.** The `.fact/`
   provider preserves identity across a move because the id was *authored*, not
   because two documents happen to hash alike. RFC 0012's prohibition on
   inferring identity from digest is respected, not circumvented.
4. **Consumers stay provider-agnostic.** `diff`, `impact` and relation
   navigation continue to consume whatever canonical `concept_id` the active
   provider emits, and need no branch for this profile.

Under this split, a `fact` context gets rename-stable identity and a plain OKF
bundle keeps the semantics RFC 0012 pins — with no capability appearing twice
and no provider quietly overriding another.

## Motivation

### The reserved documents invert the priority

`bundle.py` reserves `index.md` and `log.md` **by filename, at any depth**. A
reserved document is excluded from the `concepts` relation and lands in a
`reserved` relation carrying `path`, `filename` and an opaque `body` string.

The consequences are worth stating plainly:

- **Filename became semantics.** Any directory containing a file with that name
  gets special validation, whether or not the author intended it.
- **Reserved means unqueryable.** A log is a timeline of events — highly
  relational content — but the ordinary relational surface cannot query it.
- **The strictness points the wrong way.** `log.md` has normative rules about
  date headings and order while an ordinary concept needs only a non-empty
  `type`.
- **Rendering conventions stand in for data.** A date encoded as a heading and
  ordering encoded by document position need bespoke parsing instead of ordinary
  relational operations.

This repository already voted against the reserved log with its feet. Its own
history lives in `changelog/`, as facts of type `Release` — one document per
version, individually addressable, queryable, and carrying frontmatter — not as
date groups inside one `log.md`.

Under `fact`, `index` and `log` are not privileged filenames. When those ideas are
useful, they are types. A log entry can be an ordinary fact with a declared
timestamp; ordering becomes `ORDER BY`, and "what changed in March" becomes a
query.

### A bare type name does not say what the type means

OKF deliberately permits any non-empty string as `type`, and `fact` keeps that
contract. This is valid:

```yaml
type: Procedure
```

The limitation is not validity; it is self-description. A reader seeing
`Procedure` does not know where the type is specified, and a command-line
`--require-spec` template puts that knowledge in the caller rather than the
context.

The preferred form makes the connection explicit:

```yaml
type: .fact/specs/Procedure.md
```

For `type`, a relative specification reference is resolved from the owning
context root, not from the fact's current directory. Moving the fact therefore
does not change which type specification it names.

An externally published specification may instead use an absolute URI:

```yaml
type: https://example.org/specs/Procedure.md
```

That is useful publication and interchange, not a requirement for global
namespaces. The protocol is not part of the type's value proposition.

### The context does not describe itself today

`check --require-spec ".okf/specs/{slug}.md"` puts a fact about the context's own
structure in the caller's command line. Every consumer must remember to repeat
it, and the same context can therefore be interpreted differently depending on
how it was opened.

`fact` moves that information into `.fact/`: specifications, validation policy,
adapter declarations and optional vocabulary mappings travel with the facts they
describe.

### Why a profile now

OKF benefits from being a conservative floor. This project also needs a place to
experiment with a higher-level convention — stable identity, self-describing
types, composable contexts and adapters — without requiring the floor itself to
move at the same pace.

Some changes are also reversals rather than additions. Treating `index.md` and
`log.md` as ordinary facts deliberately un-decides semantics that OKF v0.2
assigns by filename. A profile is the appropriate place to test that choice while
keeping compatibility explicit.

### The obvious objection

A profile must not become a new serialization by accident. The relevant boundary
is the **canonical writer**, not how many inputs a permissive reader can
understand.

`fact` may read inputs that strict OKF tooling would diagnose or reject — that is
part of being permissive and of supporting adapters. But the canonical authored
form remains Markdown with YAML frontmatter, and its `type` values remain
ordinary non-empty strings. A local reference such as
`.fact/specs/Procedure.md` is still a string an OKF reader can preserve even if it
does not understand the additional convention.

If the canonical writer eventually requires syntax that an OKF Markdown reader
cannot preserve as ordinary documents and frontmatter, then `fact` has crossed
from profile into a new format and the RFC should say so explicitly.

## Principles

### Dogfooding

The repository that implements `fact` should be a `fact` context, and every
mechanism the profile offers should be exercised here. A feature nobody here uses
is a feature nobody here maintains.

Today the repository already uses several types but does not carry specification
documents for them. `--require-spec`, RFC 0006 declared column types and RFC 0007
relational contracts therefore demonstrate an existing gap: the machinery exists,
but the context does not yet make it discoverable by itself.

### Ecosystem incentive

The profile should remain implementable by people who did not write this parser.
Three constraints follow:

- **The specification ships executable conformance fixtures.** A second
  implementation proves itself against shared cases rather than undocumented
  behaviour.
- **Adapters are the contribution surface.** Supporting a foreign dialect should
  not require changing what canonical `fact` means.
- **Type specifications are addressable knowledge.** A type can point directly
  to the document that defines it. Normally that document is local to the
  context; publishing it under an absolute URI is optional.

There is one existing tension worth preserving in writing: RFC 0006 and RFC 0007
use trusted DuckDB SQL for declarations. That is convenient and precise for this
implementation but raises the cost of an independent implementation.

## Capabilities unlocked

The decisions below exist to buy five capabilities. They are the review criteria
for the mechanisms that follow.

1. **A context describes itself.** Its type specifications, rules and adapters are
   discoverable by opening the context, not by remembering caller flags.
2. **A fact's identity is independent of its location.** Moving a fact does not
   create a different fact or invalidate relations keyed by stable identity.
   Path-based Markdown references may still need repair after a move.
3. **Types can identify their own specification.** Bare OKF type strings remain
   valid; the preferred form points to an addressable specification, normally a
   local `.fact/specs/*.md` document. Absolute URIs are available when useful,
   not privileged by protocol.
4. **Contexts compose without flattening their identity.** An outer context can
   query facts from inner contexts while every fact keeps its owning context.
5. **Many readers, one canonical writer.** Any number of adapters may read
   foreign dialects; exactly one canonical form is written.

Capability 4 is especially important for vendored knowledge, multi-repository
corpora and federated relational queries: composition should not pretend all
facts were authored in one namespace.

## Decision

### 1. Every in-scope Markdown document is a fact

There is no second kind of Markdown document. No filename is reserved: within a
context's scope, a README is a fact, an RFC is a fact, and a type specification
is a fact.

The qualifier **in-scope** is load-bearing. Filesystem containment by itself is
not assertion. `.factignore` defines scope; inside that scope, Markdown documents
participate uniformly in the model.

Unreadable bytes, malformed YAML and genuine ambiguity can be errors. Other
differences from the canonical convention belong on decision 9's diagnostic
ladder.

### 2. The names are `fact` and `context`

A `fact` is one Markdown document. A `context` is the owned set of facts rooted by
`.fact/`. `bundle` and `concept` are retired from the profile vocabulary.

`fact` means "an atom asserted in this context", not "a statement guaranteed to
be true". An allegation can be a fact of the context that records the allegation;
provenance says who asserted it and from what evidence.

The intended public API direction is:

| today                        | under `fact`                         |
| ---------------------------- | ------------------------------------ |
| `bundle`                     | `context`                            |
| `concept`                    | `fact`                               |
| `concepts` relation          | `facts`, with owning `context`       |
| `concept_id`, `concept_type` | `fact_id`, `fact_type`               |
| `ConceptRecord`              | `FactRecord`                         |
| `reserved` relation          | removed                              |
| `load_bundle()`              | `open_context()`                     |
| `links`                      | `links`, unchanged                   |

### 3. A native context contains `.fact/`

`.fact/` marks a native context boundary, in the same practical sense that
`.git/` marks a repository boundary. `fact check` may discover the nearest marker
when invoked from a subdirectory.

Two statements coexist without contradiction:

- **A native authored `fact` context has `.fact/`.** That is how it describes
  itself and what `fact init` emits.
- **A markerless directory can be opened as a compatibility view.** This is how
  existing OKF bundles remain readable unchanged.

Nested `.fact/` directories create nested owning contexts rather than merging all
documents into the outer namespace.

### 4. Contexts compose without changing inner meaning

An outer context may see and query facts owned by an inner context. The `facts`
relation therefore carries owning-context identity separately from filesystem
containment.

Composition is **visibility**, not implicit semantic inheritance. An outer
context must not silently change what an inner context's type reference, alias or
validation rule means. A context should be interpretable on its own; opening it
from a higher vantage point may reveal more neighbouring facts but must not
rewrite its semantics.

If contexts later need to import vocabulary mappings or policy from another
context, that import should be explicit authored data rather than lexical
shadowing caused by directory position.

Normative results should therefore be vantage-invariant: a fact valid in its own
context does not become invalid merely because someone opened a larger tree.

### 5. Links keep ordinary Markdown semantics

A relative Markdown link resolves from the document that carries it. A
root-anchored link may resolve from the nearest owning context. Absolute URIs are
already independent of filesystem position.

A path that climbs out of a context is not inherently malformed. It is a real,
path-dependent relation with a portability cost. Moving or vendoring the context
may break it, and that is useful information to report.

The canonical writer therefore must not promise to rewrite every outward path to
an absolute URI. Such a rewrite is only possible when the target has a known
absolute identifier. Otherwise the path can be preserved and diagnosed as
non-relocatable rather than pretending a global identity exists.

Relocatability is a property that can be measured. A context whose internal links
remain internal and whose outward references use location-independent identifiers
is easier to move, but relocatability is not a prerequisite for being a context.

### 6. A fact's identity is not its path

Moving a fact can have real effects on its context. A Markdown link points to a
path, so moving its target may leave that link pointing at a location that no
longer exists. Stable identity does not make those path-dependent effects
disappear.

What a move must not do is turn the target into a different fact merely because
its location changed. A fact therefore carries a **stable id** that survives a
move within its context, and relational surfaces key on that id rather than on a
path.

This holds **inside a native context only**. For a markerless directory the
filesystem provider's identity applies unchanged, so a rename there remains
removed + added exactly as RFC 0012 specifies. The stable id is a property the
`.fact/` provider adds, never a reinterpretation imposed on plain OKF.

Identity and referential integrity are distinct invariants: a move can preserve
the first while violating the second.

A plain `git mv` may leave broken inbound links; those are observable
inconsistencies. A `fact mv` may offer a stronger operation — move the target and
rewrite inbound paths it can resolve — but that is a convenience, not a magical
property of stable identity.

The Markdown link target stays a path so documents still work as ordinary
Markdown. A stale path may be repairable when enough evidence exists, but repair
after an arbitrary filesystem change is not guaranteed by the id alone.

Whether the stable id is authored directly, pinned when first observed, or minted
by `fact init` remains open. Once it exists, it is authored identity and is never
recomputed merely because the file moved.

### 7. `type` prefers a reference to its specification

OKF's rule remains intact: any non-empty string is a valid `type` value.
`fact` adds a preferred convention rather than narrowing that contract.

```yaml
type: Procedure                           # valid OKF-style bare string
type: .fact/specs/Procedure.md            # preferred self-contained fact form
type: https://example.org/Procedure.md    # optional externally published spec
```

The preferred form is a **reference to the specification document**, not a demand
for a globally namespaced type. For `type`, a relative reference is interpreted
against the owning context root, so `.fact/specs/Procedure.md` keeps the same
meaning when the fact itself moves between directories.

An absolute URI is equally capable of identifying a specification when external
publication is useful. HTTP(S) is one possible URI scheme; the presence of a
protocol is not itself a quality signal and does not make the type preferable.

`fact init` and other canonical authoring tools should prefer a local
`.fact/specs/<name>.md` reference when the specification belongs to the context.
They may preserve an explicitly authored absolute URI when the type is genuinely
external.

### 8. Local specification resolution is distinct from network dereferencing

Resolving `.fact/specs/Procedure.md` is a local context operation. A native
self-describing context may validate whether that referenced specification exists
and whether its declaration is usable.

Fetching an absolute HTTP(S) URI is different. **Network dereferencing is never
normative.** A URL that 404s, times out or cannot be reached offline does not by
itself make the context non-conformant. Remote fetching is optional enrichment and
may be cached under `.fact/cache/`.

This distinction keeps self-description strong without making validity depend on
someone else's DNS, uptime or authentication boundary.

### 9. Permissiveness is a graded ladder, not a binary

A rule that is either silent or fatal cannot express "this works, but there is a
more informative convention." `fact` diagnostics therefore carry four levels:

| level   | meaning                                                              |
| ------- | -------------------------------------------------------------------- |
| `error` | the context is ambiguous or unreadable                                |
| `warn`  | well-formed, but intended meaning cannot be recovered reliably         |
| `info`  | well-formed and recoverable, but not the preferred authored convention |
| `hint`  | canonical, with a non-semantic improvement available                   |

Applied to `type`, an initial policy can be:

| authored `type`                         | level  | why                                        |
| --------------------------------------- | ------ | ------------------------------------------ |
| `.fact/specs/Procedure.md` and exists   | —      | preferred self-contained form              |
| absolute URI to an external spec        | —/info | explicit and addressable; network optional |
| `Procedure`, local spec inferable       | `info` | valid OKF and recoverable                  |
| `Procedure`, no specification known    | `warn` | valid OKF, but context does not explain it |
| relative specification target missing  | `warn` | intent clear, referenced local fact absent |

The exact default levels remain policy, but a bare string must never become a
parse error merely because `fact` prefers a specification reference.

Levels are per-rule and declared in `.fact/rules.yaml`; CI can choose a threshold
with `--fail-on <level>` rather than proliferating one normative flag per rule.
Diagnostic code and diagnostic level are separate concepts.

### 10. Type identity and relational naming are separate concerns

A referenced specification is a good semantic identifier and still may be a poor
SQL table name. RFC 0007 therefore must not blindly use the authored `type` string
as a physical relation name.

The semantic side should preserve the resolved type reference: for a local type,
that is the context-relative specification path; for an external type, it can be
an absolute URI.

The relational side may use a derived or explicitly assigned local name. A simple
basename such as `Procedure` is fine when it is unique. If two distinct
specifications would both project to `Task`, the projection must not collapse them
silently; an explicit alias or a collision-free qualified name is required.

This keeps the useful part of namespacing — **no silent collision in relational
surfaces** — without making global namespaces a prerequisite for ordinary local
types.

### 11. `.fact/` makes the context self-describing

```text
.fact/
  specs/<name>.md           # preferred type specification facts
  specs/<name>.schema.sql   # RFC 0006 declared column types
  schema.sql                # RFC 0007 context-wide relational contract
  vocabulary.yaml           # optional aliases / foreign vocabulary mappings
  rules.yaml                # per-rule diagnostic levels
  adapters.yaml             # declared source adapters
  cache/                    # optional cached remote enrichment
```

The central point is discovery: a consumer opens the context and can find the
specifications and policies it needs. `--require-spec`/`--spec-template` may
remain as compatibility or override mechanisms, but they are not how a native
context explains itself.

### 12. `.fact/` is not a magic zone for Markdown

Decision 1 has no exception for the profile's own directory. Markdown documents
under `.fact/specs/` are **ordinary facts**, discovered, parsed and queryable.
The specification of a type is itself a fact about that type.

Non-Markdown files such as `schema.sql`, `rules.yaml` and cache entries are
sidecars. The axiom is that in-scope Markdown documents are facts, not that every
file must be Markdown.

### 13. Compatibility with foreign specifications has separate mechanisms

"Compatible with other specs" collapses distinct jobs:

- **Vocabulary aliasing.** Foreign field names or type identifiers can map onto
  local concepts. This is declarative and can live in `vocabulary.yaml`.
- **Structural adaptation.** Wikilinks, JSON records, manifests or other shapes
  need readers, not simple renaming.
- **Projection for a foreign validator.** Producing a representation accepted by
  a third-party validator is a serializer and may be lossy.

Only declarative vocabulary mapping belongs in the core. Structural readers and
foreign projections are adapters. An adapter must not be able to redefine what
the canonical writer emits.

### 14. Many readers, one writer

A format compatible with everything on both ends is unimplementable. The
asymmetry is the constraint that keeps `fact` finite: **any number of adapters may
read; exactly one canonical form is written.**

Two readers disagreeing about a foreign dialect is an adapter bug. Two canonical
writers disagreeing about what `fact` itself emits is a fork.

## Relationship to OKF v0.2

The relationship is deliberately asymmetric:

- **OKF → `fact` is total for valid OKF.** A valid OKF bundle remains readable as
  a `fact` compatibility view without being rewritten.
- **`fact` may read more than strict OKF.** Permissive parsing and adapters are
  allowed to recover useful intent from inputs that are not themselves conformant
  OKF.
- **Canonical `fact` stays in the OKF serialization family.** Facts are Markdown
  with YAML frontmatter and `type` remains a non-empty string. A value such as
  `.fact/specs/Procedure.md` can be preserved by an OKF reader even if the reader
  does not implement the reference convention.
- **Projection may still be lossy.** In particular, a consumer that insists on
  OKF v0.2 reserved `index.md`/`log.md` semantics may need synthesized output from
  ordinary fact types.

Round-tripping every profile feature through every OKF tool is not promised.
Preserving the underlying Markdown/frontmatter representation is.

## Non-goals

- A new serialization. `fact` remains Markdown with YAML frontmatter.
- Making HTTP(S) or any other URI scheme normative for type identity.
- Requiring network access on validation paths.
- Replacing RFC 0006 or RFC 0007. The profile makes their declarations
  discoverable and attachable to addressable type specifications.

## Open questions

- Whether the profile-local vocabulary and reserved-filename decisions should
  ever migrate into base OKF, and what migration would make that safe.
- Whether `index` survives as a useful type or a context description is simply a
  fact like any other.
- The exact stable-id minting/pinning mechanism for facts.
- Whether explicit cross-context imports are needed for shared policies or
  vocabulary mappings, and what their minimal syntax should be.
- Which foreign vocabulary mappings, if any, should ship as built-ins.
