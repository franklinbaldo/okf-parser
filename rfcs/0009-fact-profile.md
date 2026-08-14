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

## Decision

### 1. Everything is a `.md`, and every `.md` is a fact

There is no second kind of document. No filename is reserved, no directory is
magic, and no Markdown file below the context root is outside the model. A README
is a fact. An RFC is a fact. A type's own specification document is a fact.

This is the axiom the rest of the profile follows from, and it is what makes
decision 12 unavoidable rather than merely tidy.

It also dissolves a problem the current implementation had to build a feature
around. `README.md` explains that "a repository that keeps OKF knowledge next to
code, a README and vendored dependencies has no root that validates cleanly,"
because every unrelated Markdown file raises `OKF001`. That is a consequence of
treating some `.md` files as data and the rest as trespassers. Once every `.md`
is a fact, an unrelated file is not invalid — it is a fact whose type nobody
declared, which decision 9 reports at `warn` and never as an error.

`.factignore` therefore keeps existing, but its meaning changes: it selects
**scope**, not validity. Excluding a vendored dependency says "these facts are
not mine," not "these files are malformed." The distinction matters because the
current framing forces a choice between an unvalidatable root and unresolvable
cross-context links.

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

The marker is **recommended, not required**, because decision 1 and the
permissiveness commitment both forbid making it a precondition — no OKF bundle
has a `.fact/` directory, and every OKF bundle must remain readable. A directory
with no marker is an **implicit context** rooted where the caller pointed,
running on defaults, reported at `hint`. This repository's own
`examples/minimal/` is exactly that case, and remains one until it earns a
marker.

### 4. A context is always a context *of* something

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

Facts must be reorganizable inside their context without breaking anything. A
directory that grows from twenty documents into subdirectories is ordinary
housekeeping, and it must not invalidate a single link.

Today it invalidates all of them. `concept_id` is derived from the path, so
moving a document changes its identity, and every link pointing at it breaks
along with every relation keyed on it. Decision 5's root-anchoring only fixes
half: it survives the *source* moving, not the *target* moving.

A fact therefore carries a **stable id** that survives any move within its
context, and the relational surfaces key on that id rather than on a path.

The Markdown link target stays a path. This is deliberate and it is a
concession: an id-shaped target renders as a dead link in every ordinary Markdown
viewer, and a format whose documents do not read as documents on GitHub has lost
something worth more than the purity. So the path is how the link is *written*
and the id is what the link *means* — the parser resolves the path once,
records the edge by id, and the path becomes a repairable cache of the relation
rather than the relation itself.

That repairability is the payoff. `fact mv` moves a document and rewrites every
inbound path in the context, because it knows the edges by id. A link left stale
by a plain `git mv` is likewise **repairable** rather than merely reported: the
target still exists under the same id, so the warning carries a fix instead of a
complaint. `OKF101` stops being an observation and becomes an action.

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

`.fact/vocabulary.yaml` therefore carries a prefix map, and the **local name** is
the CURIE's suffix under a declared prefix. Identity is the URI; the local name
is what reaches DuckDB, paths and reports.

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
