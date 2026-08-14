---
type: RFC
title: The fact profile
status: draft
description: A conservative superset of OKF v0.2 naming its atom a fact and its scope a context, with URI types, a self-describing .fact directory, no reserved filenames, and a declared adapter boundary for foreign specifications
---

# RFC 0009: The fact profile

## Summary

OKF v0.2 is a good floor and a poor ceiling. It requires almost nothing of a
concept — a non-empty `type` — and then spends its strictness on the two
documents that carry the least structured information, `index.md` and `log.md`.
This RFC proposes `fact`: a profile that keeps every OKF bundle readable, moves
the strictness to where the data is, and makes a context describe itself instead of
depending on flags its consumers must remember to pass.

`fact` is **opinionated in what it emits and permissive in what it accepts**. It
recognizes exactly one canonical form and reports every other convention on a
graded ladder rather than rejecting it, so adopting the profile is a gradient a
context walks at its own pace instead of a cliff it falls off.

## Motivation

### The reserved documents invert the priority

`bundle.py` reserves `index.md` and `log.md` **by filename, at any depth**. A
reserved document is excluded from the `concepts` relation and lands in a
`reserved` relation carrying `path`, `filename` and an opaque `body` string.

The consequences are worth stating plainly:

- **Filename became semantics.** Any directory containing a file with that name
  gets special validation. This is the identity-follows-layout coupling that
  `type_specs.py` rejects in its own module docstring, applied to the two
  documents nobody chose to opt in.
- **Reserved means unqueryable.** A log is a timeline of events — the most
  relational content in a context. It is also the only content no relational
  surface can reach. Answering "what changed in March" requires re-parsing
  Markdown by hand, in a project whose entire premise is that context-wide
  questions should be relational queries.
- **The strictness points the wrong way.** `log.md` must use `YYYY-MM-DD` level-two
  headings, they must be real dates, and they must be ordered newest first
  (`OKF008`, `OKF009`) — all normative errors. A concept document, which carries
  actual structured data, must only have a non-empty `type`. The free-prose file
  is validated strictly; the data is anarchy.
- **The format is machine-hostile on purpose.** A date encoded as a heading and
  an ordering encoded as a rendering convention is a schema in costume, kept
  alive by a bespoke validator.

This repository already voted against the reserved log with its feet. Its own
history lives in `changelog/`, as **55 facts of type `Release`** — one document
per version, individually addressable, queryable, and carrying frontmatter — not
as date groups inside a `log.md`. The project reached for the per-document model the
moment it needed its own history to be usable, and the spec's answer for
histories went unused in the one repository that implements the spec.

Under `fact` these documents are not special. `index` and `log` are **types**, so
a log entry is an ordinary fact with a declared timestamp: ordering becomes
`ORDER BY` rather than a normative error, "what changed in March" becomes a
`WHERE` clause, and `_validate_log` and `_validate_index` disappear without a
replacement — not because the constraint was relaxed, but because it moved into
the type's declaration where every other constraint already lives.

### A type name is not an identifier

`type` is an arbitrary non-empty string, so two projects both using `Procedure`
collide silently, and nothing can say what either means. The derived-path
machinery makes this concrete: `type_slug()` is documented as non-injective —
`Revisão Ciência` and `Revisao Ciencia` both slug to `revisao-ciencia` — and RFC
0006 has to raise an explicit ambiguity error rather than let one type borrow
another's declaration.

### The context does not describe itself

`check --require-spec ".okf/specs/{slug}.md"` puts a fact about the context's own
structure in the caller's command line. Every consumer must remember to repeat
it, and the same context validates differently depending on who ran the command.

### The specification over-decides in one place and abstains in the other

The two defects above are not independent. OKF v0.2 spends normative strictness
on a free-prose file — `log.md` must use `YYYY-MM-DD` level-two headings, they
must be real dates, they must run newest first, three separate errors — and then
declines to decide anything at all about the documents carrying structured data,
where `type` may be any non-empty string and no field is specified.

Both halves are the same mistake pointed in opposite directions. The strictness
is spent where a schema was never going to help, and withheld where one is the
entire point. A floor that decides a heading's date format but not what a type
means has chosen the easy commitments.

### Why a profile now, rather than patience

The obvious alternative is to propose all of this upstream and wait. Three
things argue against waiting, and only the third is about anyone's competence.

**The steward's tempo is not this project's tempo.** OKF is stewarded inside
Google Cloud — the specification lives in `GoogleCloudPlatform/knowledge-catalog`
— and a large vendor moving deliberately through review is behaving correctly for
a format meant as a stable floor. It is also a tempo no downstream project can
plan around. Conservatism is a virtue in a floor and an obstacle in a ceiling,
which is precisely why the two should not be the same document.

**A specification living inside a product repository inherits that product's
priorities.** This is structural, not an accusation: when the spec and a
commercial knowledge-catalog service share a home, the questions that get
answered first are the ones the service needs answered. `fact` needs decisions
about composition, identity and vocabulary that no service is currently asking
for. A profile can take them without asking anyone to reprioritize.

**Some of what this RFC needs is not a gap but a reversal.** Removing the
reserved `index.md` and `log.md` is not an addition upstream could accept
compatibly; it un-decides something v0.2 already decided. That belongs in a
profile by construction.

### The obvious objection

[xkcd 927](https://xkcd.com/927/) is the correct first reaction to any document
that proposes a new format, and it deserves an answer rather than a joke back:
fourteen competing standards, someone writes a fifteenth to unify them, now there
are fifteen.

The answer is the asymmetry in the "Relationship to OKF v0.2" section, and it is
falsifiable rather than rhetorical. `fact` does not compete with OKF for the
same documents: every OKF bundle is a valid `fact` context unchanged, and
`fact export --okf` emits a bundle `okflint` and `okf-cli` accept. A reader
choosing between them is choosing a reading of the same files, not a format for
new ones — which is what makes this a profile rather than a competitor.

Where the cartoon does land: the moment `fact` accepts a document OKF rejects,
or emits one OKF cannot read, it *is* a fifteenth standard, whatever the
introduction claims. That is why decision 14's "many readers, one writer" and
the OKF-projection promise are load-bearing rather than courtesies. If either
breaks, the objection is simply correct and this RFC should be withdrawn.

## Principles

Two commitments decide the arguments this RFC would otherwise have to relitigate
at every decision.

### Dogfooding

The repository that implements `fact` is a `fact` context, and every mechanism the
profile offers is one this repository uses on itself. A feature nobody here runs
is a feature nobody here maintains.

Measured against that standard today, the gaps are concrete rather than
rhetorical. The repository uses eight types — `Architecture`, `BenchmarkResult`,
`ChangelogEntry`, `Documentation`, `Procedure`, `Project`, `RFC`, `Release` — and
has **no specification document for any of them**. CI runs a bare
`okf-parser check .`, so `--require-spec` (0.14.0), the RFC 0006 declared column
types, and the RFC 0007 relational contract are all shipped, tested, documented,
and unused by their own author. Two of those eight types, `ChangelogEntry` and
`Release`, plausibly describe the same thing, which is exactly the drift a
specification document exists to catch.

Adopting `fact` here is therefore not a demonstration bolted on afterwards. It is
the first consumer, and its `.fact/` directory is the profile's real conformance
test.

### Ecosystem incentive

The profile has to be implementable by people who did not write it, or it is a
private convention with delusions. Three consequences follow, and this RFC treats
them as constraints rather than aspirations:

- **The specification ships executable conformance fixtures.** `conformance/`
  already carries shared Python/TypeScript fixtures for exclusion, formatting,
  frontmatter and schema inference. A third implementation proves itself against
  those files rather than against this repository's behaviour.
- **Adapters are the contribution surface.** Decision 14's "many readers, one
  writer" exists partly so that supporting a foreign dialect never requires
  touching the core or negotiating with its maintainer.
- **Type vocabularies are a public good.** URI types (decision 7) let anyone
  publish a vocabulary at their own domain without permission from this project.
  A type namespace nobody controls is the difference between an ecosystem and a
  plugin directory.

There is one honest tension. RFC 0006 and RFC 0007 make the declaration format
"trusted DuckDB SQL, full stop," which means a conforming implementation must
embed DuckDB — a heavy demand on a third implementation, and the single largest
barrier to entry the profile currently imposes. The choice was right for what
those RFCs needed (a physical type should be DuckDB's own type, not a grammar
re-derived here), but it is a cost paid in ecosystem breadth and should be
recorded as such rather than discovered later by whoever tries to write the
second implementation.

## Capabilities unlocked

The decisions below exist to buy five capabilities. Stating them first makes the
argument run capability → mechanism instead of mechanism → consequence, and gives
every later decision a review criterion: is it **necessary** for one of these, is
it **one possible implementation** of something necessary, or is it accidental
complexity that can be deferred?

1. **A context describes itself.** What its types are, how its vocabulary
   resolves and how strict it wants to be are properties of the context, readable
   by opening it — not flags each consumer must remember.
2. **A fact's identity is independent of its location.** Documents can be
   reorganized inside a context without invalidating links, relations, or
   anything keyed on them.
3. **Vocabularies are globally identifiable and locally projectable.** A type is
   identified by a URI that anyone can publish, and still lands in a relational
   surface as a workable local name — without losing distinguishability in the
   projection.
4. **Contexts compose without flattening their identity.** An outer context can
   see and query facts from inner ones while every fact keeps its owning context.
5. **Many readers, one canonical writer.** Any number of adapters may read
   foreign dialects; exactly one canonical form is written.

Capability 4 is the one with no counterpart in OKF v0.2, and it is what makes
vendored knowledge, multi-repository corpora and federated relational queries
expressible without pretending every fact was authored in one namespace.

## Decision

### 1. Every in-scope Markdown document is a fact

There is no second kind of document. No filename is reserved and no directory is
magic: within a context's scope, a README is a fact, an RFC is a fact, and a
type's own specification document is a fact.

The qualifier is load-bearing. The claim is *not* that every `.md` under some
filesystem root is a fact by virtue of sitting there — that would make
containment itself an assertion mechanism, so dropping a directory into a tree
would silently assert everything inside it. Membership in a context is
intentional. Scope is declared; inside it, uniformity is absolute.

This is the same containment-is-not-membership distinction decision 4 draws for
subcontexts, applied at the outer edge instead of at an inner boundary. One rule
covers both: **being inside the tree is not being part of the context.**

This is the axiom the rest of the profile follows from, and it is what makes
decision 12 unavoidable rather than merely tidy.

It also dissolves a problem the current implementation had to build a feature
around. `README.md` explains that "a repository that keeps OKF knowledge next to
code, a README and vendored dependencies has no root that validates cleanly,"
because every unrelated Markdown file raises `OKF001`. That is a consequence of
treating some `.md` files as data and the rest as trespassers. Once every `.md`
is a fact, an unrelated file is not invalid — it is a fact whose type nobody
declared, which decision 9 reports at `warn` and never as an error.

`.factignore` therefore keeps existing, and its job becomes the honest one: it
**defines the context's scope**. It does not separate real facts from special
documents, because inside the scope there are no special documents. Excluding a
vendored dependency says "these facts are not mine," never "these files are
malformed." The distinction matters because the current framing forces a choice
between an unvalidatable root and unresolvable cross-context links.

The cost is deliberate: `fact` has almost no parse-level errors. Unreadable
bytes, malformed YAML, and ambiguity are errors. Everything else is a level on
decision 9's ladder.

### 2. The names are `fact` and `context`

A `fact` is one Markdown document. A `context` is the set of facts a `.fact/`
directory roots. `bundle` and `concept` are retired.

`concept` contradicts decision 1 outright: a README is not a concept, and neither
is a changelog entry or an issue comment, but all three are facts. It also
collides with `skos:Concept`, which has a precise meaning in published controlled
vocabularies — the exact neighbourhood URI types invite this profile into. And it
promises taxonomy, which this project's own README opens by refusing to impose.

`bundle` says packaging. Nothing here is packaged or unpacked; what the word has
to name is the region within which links resolve and constraints hold. That is a
context.

**`fact` means "ground atom asserted in this context", not "true statement".**
This is the Datalog reading, and it is stated here so the argument does not have
to happen later: what makes something a fact is having been asserted in this
context, not corresponding to the world. An allegation is a fact of the context
that asserts it. This is precisely why provenance is a first-class primitive
rather than decoration — a context records who asserted what, never what is true.

The public API rename is mechanical:

| today                        | under `fact`                |
| ---------------------------- | --------------------------- |
| `bundle`                     | `context`                   |
| `concept`                    | `fact`                      |
| `concepts` relation          | `facts`, plus an owning `context` column |
| `concept_id`, `concept_type` | `fact_id`, `fact_type`      |
| `ConceptRecord`              | `FactRecord`                |
| `reserved` relation          | removed by decision 1       |
| `load_bundle()`              | `open_context()`            |
| `links`                      | `links`, unchanged          |

`links` stays. When a link gains a predicate the temptation will be to promote it
to `assertion` or `edge`, but `link` is honest about what the Markdown contains,
and the predicate is an attribute of the link rather than a new noun.

### 3. A context is a directory that contains `.fact/`

The marker defines the boundary, the way `.git/` defines a repository. Three
consequences, each replacing something currently done by hand:

- **The root is discovered, not passed.** `fact check` run from any subdirectory
  walks up to the nearest `.fact/`, exactly as `git status` does. Today the root
  is an argument every consumer must get right.
- **Nesting is well defined.** A `.fact/` inside a context roots an inner context
  that owns its own documents. This resolves the dilemma `README.md` currently
  describes at length: checking a repository root drowns in unrelated Markdown,
  while checking each bundle separately makes every link *between* them
  unresolvable. With markers the outer context sees an inner one as a context,
  not as loose files.
- **Vendored knowledge excludes itself.** A dependency that carries its own
  `.fact/` is already someone else's context and needs no pattern in
  `.factignore`. Structure replaces configuration for the case that motivated the
  configuration.

Two things are true at once, and the profile is sharper for stating them
separately rather than blurring them into "recommended":

- **A native `fact` context has `.fact/`.** It is the canonical authored form,
  the thing `fact init` creates, and the only arrangement in which a context can
  describe itself at all. A self-describing context without the mechanism that
  makes it self-describing is a contradiction, not a relaxation.
- **A directory without `.fact/` is read as an implicit compatibility view.** It
  is rooted where the caller pointed, runs on defaults, and reads perfectly well
  — which is what keeps the `OKF → fact` promise total, since no OKF bundle has
  a `.fact/` directory and every one of them must remain readable.

This is the emit/accept split from the Summary applied to structure instead of
syntax: markerless input is accepted without complaint, and markerless output is
never emitted. The compatibility view is a *reading* of someone else's
arrangement, not an authored form anyone should be producing.

This repository's own `examples/minimal/` is deliberately such a view — it exists
to show the smallest thing OKF accepts, so giving it a marker would misrepresent
what it demonstrates.

### 4. A context is always a context *of* something

**The capability first: contexts compose without flattening their identity.** An
outer context can see and query facts that inner contexts own, and every fact
keeps its owning context through the composition. Nothing is merged into one
namespace, nothing is copied, and no fact loses the answer to "whose is this?"

That is what makes vendored knowledge, multi-repository corpora and federated
relational queries expressible at all. Today the only way to query across two
bundles is to treat their union as one bundle, which asserts something false —
that everything in it was authored together — and destroys the distinction on the
way in. Everything below (visibility, ownership, inheritance, shadowing, and
decision 5's link resolution) is machinery derived from this capability, and each
piece should be judged by whether it serves it.

Context is relative, not absolute. There is no privileged root — only the vantage
point a caller opened from. Every fact therefore sits in a **chain** of contexts,
from the nearest enclosing `.fact/` outward, and "the context" of a fact names a
position in that chain rather than a single directory.

Two relations the word otherwise conflates come apart here:

- **containment** — the fact is inside this directory tree;
- **membership** — the fact belongs to this context's own set.

A fact contained in a subcontext is *visible* from the outer context and
*belongs* to the inner one. The `facts` relation therefore carries its owning
context, and a query scopes by that column instead of by an exclusion pattern.

This dissolves the dilemma `README.md` currently states as unavoidable. Today a
repository must choose between checking the root, where every unrelated Markdown
file is noise, and checking each bundle separately, which makes every link
*between* bundles unresolvable — "the only arrangement under which cross-bundle
link validation runs at all." With relative contexts there is no choice to make:
links resolve across the whole tree because the outer vantage point sees it, and
ownership stays honest because every fact reports which context it belongs to.
Vendored knowledge is not excluded; it is **attributed**.

Resolution is lexical scoping in the ordinary programming-language sense. A
prefix, an alias or a rule level declared in an outer `.fact/` is visible to
every context inside it, and an inner declaration shadows an outer one for its
own subtree. Nothing is inherited by copying, so an outer context never has to
enumerate what its subcontexts contain.

One invariant makes vantage-relativity safe: **normative results are
vantage-invariant.** A fact that is an error inside its own context is an error
from every context containing it, and a valid fact never becomes invalid because
someone opened the tree from higher up. Levels may vary — a type whose prefix is
declared two levels up reads canonical from outside and `warn` from inside,
which is true and useful information — but conformance may not.

### 5. Links resolve against the nearest context

Three forms, and the third is what keeps the first two honest:

| form                        | resolves against              |
| --------------------------- | ----------------------------- |
| `../other.md`               | the document carrying it      |
| `/other.md`                 | the nearest enclosing context |
| `https://example.org/other` | nothing — it is already global |

A relative link resolves from its own document, as Markdown already says. A
root-anchored link resolves from the **nearest enclosing context**, never from the
vantage point the caller opened. An absolute URI resolves against no context at
all.

This is what makes decision 4's relativity safe in practice. If root-anchored
links resolved against the vantage point, the same document would point at
different targets depending on where someone ran the command, and a subcontext
could not be moved without rewriting its contents. Anchoring to the nearest
`.fact/` makes a context **relocatable**: it can be vendored into another tree,
extracted out of one, or published on its own, and its internal links keep
meaning the same thing.

**A path that climbs out of its nearest context is an aberration, not a feature.**
`../../../etc.md` states a fact about where the context happens to sit in
someone's filesystem, which is not knowledge and does not survive the context
being moved, vendored or published. The canonical writer never emits one, and
`fact format` rewrites it into the absolute form of the same target.

It is reported at `warn` rather than `error`, and the reason is the ladder's own
rule: an escaping path is unambiguous and readable, so calling it an error would
make level track distaste instead of distance from canonical. A context that has
finished tidying sets `--fail-on warn` and gets the stricter behaviour without
the profile pretending the file could not be understood.

Referring outward is therefore what the absolute form is for. It
names its target globally, so it means the same thing at every position in every
tree — the same property decision 7 buys for types, through the same mechanism.
It is also subject to the same rule as a type URI: dereferencing it is never
normative, and an absolute link nobody can fetch is advisory, exactly as an
unresolved relative link is.

Relocatability is therefore a property a context either has or lacks, and it is
checkable rather than aspirational: **a context is relocatable exactly when every
reference leaving it is absolute.**

### 6. A fact's identity is not its path

Moving a fact can have real effects on its context. A Markdown link points to a
path, so moving its target may leave that link pointing at a location that no
longer exists. Stable identity does not make those path-dependent effects
disappear, and the profile should not pretend otherwise.

What a move must not do is turn the target into a different fact merely because
its location changed. Today `concept_id` is derived from the path, so one
filesystem operation conflates two distinct events: the fact changes identity,
and inbound path references may break. Under `fact` those concerns are separate.

A fact therefore carries a **stable id** that survives a move within its context,
and relational surfaces key on that id rather than on a path. The path remains
its authored location, and ordinary Markdown links remain path references. In
other words, **identity and referential integrity are distinct invariants**: a
move can preserve the first while violating the second.

This distinction gives different operations useful, honest semantics. A plain
`git mv` moves the file and may leave broken inbound links; those are observable
inconsistencies in the resulting context and should be reported as such. A
`fact mv` may offer a stronger transactional operation: move the target and
rewrite the inbound links it can resolve, with a postcondition that references
known before the move remain valid. That stronger operation is a convenience
built on knowledge of the context, not a consequence of stable identity itself.

The Markdown link target therefore stays a path. An id-shaped target would render
as a dead link in ordinary Markdown viewers, and a format whose documents no
longer read as documents on GitHub has lost something worth more than that
purity. The stable id tells relational consumers *which fact this is*; the path
tells Markdown readers *where it is now*.

A stale path may be repairable when the tool has enough evidence to identify the
moved target, but repair after an arbitrary filesystem change is not guaranteed
by the id alone. The important guarantee is narrower: changing location does not
by itself change identity.

Whether the id is authored in frontmatter, derived from the first path and then
pinned, or minted by `fact init`, is left open — but it is authored data once it
exists, never recomputed from location.

### 7. `type` prefers a URI, ideally a dereferenceable URL

Three spellings are accepted:

```yaml
type: Procedure                          # OKF-style bare string
type: fact:Procedure                     # CURIE resolved through the context
type: https://okf.dev/types/Procedure    # full URI
```

CURIE and full URI are the canonical forms `fact init` and `fact format` emit.
Every other convention stays readable; how loudly a non-canonical one is
reported is decision 9's ladder, and the context chooses where it sits on that
ladder.

### 8. Dereferencing is never normative

A `type` URL that 404s, times out, or is unreachable because the machine is
offline **does not affect conformance**. Dereferencing is optional enrichment,
cached under `.fact/cache/`, and no validation path may require it. Making
context validity depend on someone else's DNS would be a worse defect than
anything this RFC repairs.

### 9. Permissiveness is a graded ladder, not a binary

A rule that is either silent or fatal cannot express "this works, but there is a
better way." `fact` diagnostics therefore carry four levels:

| level   | meaning                                                            |
| ------- | ------------------------------------------------------------------ |
| `error` | the context is ambiguous or unreadable; no configuration silences it |
| `warn`  | well-formed, but the intent cannot be recovered by a consumer       |
| `info`  | well-formed and recoverable, but not the canonical convention       |
| `hint`  | canonical, with a stylistic improvement available                   |

The rule that keeps the assignment from being arbitrary: **a level measures
distance from canonical, not gravity of sin.** Applied to `type`:

| authored `type`                              | level   | why                                       |
| -------------------------------------------- | ------- | ----------------------------------------- |
| `https://okf.dev/types/Procedure`            | —       | canonical                                 |
| `fact:Procedure` under a declared prefix     | —       | canonical                                 |
| `Procedure`, declared in `.fact/types/`      | `info`  | meaning is local but recoverable          |
| `Procedure`, undeclared                      | `warn`  | nothing in the context says what it means  |
| two types sharing one local name             | `error` | ambiguity, not style                      |

The last row is the load-bearing one. Ambiguity survives maximal permissiveness
because no amount of tolerance tells a consumer which of two types a declaration
belongs to — which is why RFC 0006 already raises on a shared derived path.

Levels are per-rule and declared in `.fact/rules.yaml`, so a context states its
own adoption stage instead of every consumer passing flags. CI needs one knob,
`--fail-on <level>`, rather than one flag per rule; `--normative-spec` and the
`--normative-uri` an earlier draft of this RFC proposed are what that
proliferation looks like.

**A rule may never be introduced at `error` in a minor release.** New rules enter
at `info` or `warn` and are promoted only on a major version. Without that,
"permissive" is a promise broken by the next release that adds a check.

This has a consequence for the existing diagnostic codes. `OKF0xx` currently
means error and `OKF1xx` means warning — the numbering encodes severity. Once
levels are configurable that encoding becomes false, so under `fact` the code
identifies the **rule**, the level is a separate configurable attribute, and
`Severity` grows from two values to four.

### 10. Global identity and local name are separate

A URI identifies a type globally; it is not usable as a table name, a filename,
or a column name. RFC 0007 defines the relational table name as the exact
authored `type` value, and `CREATE TABLE "https://okf.dev/types/Procedure"` is
legal DuckDB and indefensible. Worse, truncating to the last segment reintroduces
the collision the URI was adopted to remove: `https://a.example/Task` and
`https://b.example/Task` share it.

`.fact/vocabulary.yaml` therefore carries a prefix map, and every type gets a
**local name** that is *assigned*, never truncated out of the URI. Identity is
the URI; the local name is what reaches DuckDB, paths and reports.

The rule that keeps the assignment sound: **a default must be collision-free by
construction, not by luck.** So the default local name is prefix-qualified —
`a:Task` and `b:Task` become `a_Task` and `b_Task`, and stay distinguishable
exactly as their URIs are. Taking the CURIE suffix by default would recreate the
collision the URI was adopted to remove, one paragraph after this decision
rejects that very move for the last URI segment.

A context may assign an explicit alias in `vocabulary.yaml`, and the bare suffix
is available that way — `a:Task` as `Task` is a fine thing to write when the
context has only one `Task` and the author knows it. What is not available is
*silently* landing there. Two types resolving to one local name without an alias
is the ambiguity error decision 9's ladder reserves as unsilenceable.

The capability being protected is worth naming: **a global identity can be
projected into local relational surfaces without losing distinguishability.**

### 11. `.fact/` makes the context self-describing

```text
.fact/
  vocabulary.yaml           # prefix map, foreign vocabulary aliases
  rules.yaml                # per-rule diagnostic levels
  adapters.yaml             # declared source adapters
  types/<name>.md           # the type's specification document
  types/<name>.schema.sql   # RFC 0006 declared column types
  schema.sql                # RFC 0007 context-wide relational contract
  cache/                    # dereferenced remote specifications, offline-first
```

This subsumes the `--require-spec` and `--spec-template` flags: a consumer opens
the directory and discovers the arrangement rather than being told about it. The
flags remain, for compatibility and for overriding.

### 12. `.fact/` is not a magic zone

Decisions 1 and 3 admit no exception for the profile's own directory. Documents under
`.fact/types/` are **ordinary facts**, discovered, parsed and queryable like any
other. The specification of a type is a fact about a type.

The axiom is that every `.md` is a fact, not that every file is Markdown.
`vocabulary.yaml`, `schema.sql` and `cache/` are sidecars, not documents, and they
carry no facts of their own — the same arrangement RFC 0006 already uses when it
derives `rotina.schema.sql` beside `rotina.md`.
Reserving `.fact/` as a region invisible to queries would reproduce exactly the
`index.md`/`log.md` mistake this RFC exists to correct.

### 13. Compatibility with foreign specifications is three mechanisms, not one

"Compatible with other specs" collapses three distinct things, and conflating
them is how a format acquires an unimplementable surface:

- **Vocabulary aliasing.** Another specification's field names map onto `fact`'s.
  `dc:title` is `title`; `schema:name` is `title`. This is pure renaming, it is
  declarative, and it belongs in `.fact/vocabulary.yaml`. Once `type` is a URI
  resolved through a prefix map, this mechanism is nearly free.
- **Structural adaptation.** The foreign shape differs — wikilinks instead of
  Markdown links, a `datapackage.json` manifest instead of per-document
  frontmatter, JSON records instead of Markdown. This needs a reader, not a
  mapping.
- **Projection for a foreign validator.** Emitting output that a third-party
  validator accepts (`okflint`, a Frictionless validator, SHACL). This is the
  lossy direction and is a serializer.

**Only the first belongs in the core.** The second and third are adapters, and an
adapter must never be able to change what "valid `fact`" means. This is not a new
rule for this repository — `docs/architecture.md` already draws exactly this
boundary between a strict core and source adaptation. This RFC promotes it from
an internal architecture note to a mechanism the context declares.

### 14. Many readers, one writer

A format compatible with everything on both ends is unimplementable. The
asymmetry is the constraint that keeps `fact` finite: **any number of adapters may
read; exactly one canonical form is written.** Two readers disagreeing about a
foreign dialect is a bug in one adapter. Two writers disagreeing is a fork.

## Relationship to OKF v0.2

The relation is deliberately asymmetric, and calling it a superset is only
accurate in one direction:

- **OKF → `fact` is total.** Every valid OKF bundle is a valid `fact` context,
  unchanged, and reads with advisory diagnostics at worst. This is the sense in
  which `fact` is a superset.
- **`fact` → OKF is a lossy projection.** `fact export --okf` emits a conformant
  OKF bundle: typed `LogEntry` facts render back into a conforming `log.md`,
  URI types render to their local names. The types do not survive the round trip.

Round-tripping is explicitly **not** promised. What is promised is that `fact`
reads any OKF bundle and can emit one that `okflint` and `okf-cli` accept.

## Non-goals

- A new serialization. `fact` is Markdown with YAML frontmatter, as OKF is.
- Replacing RFC 0006 or RFC 0007. Both are `fact` mechanisms already; this RFC
  moves their declarations into `.fact/` and gives them a type identity worth
  attaching to.
- Network access on any validation path.

## Open questions

- Whether `index` as a type survives at all, or whether a context-level
  description is just a fact like any other.
- Whether the `.fact/vocabulary.yaml` prefix map should adopt JSON-LD `@context`
  syntax outright rather than a lookalike.
- Which foreign vocabulary ships as a built-in alias set, if any.