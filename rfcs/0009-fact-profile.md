---
type: RFC
title: The fact profile
status: draft
description: A conservative superset of OKF v0.2 with URI types, a self-describing .fact directory, no reserved filenames, and a declared adapter boundary for foreign specifications
---

# RFC 0009: The fact profile

## Summary

OKF v0.2 is a good floor and a poor ceiling. It requires almost nothing of a
concept — a non-empty `type` — and then spends its strictness on the two
documents that carry the least structured information, `index.md` and `log.md`.
This RFC proposes `fact`: a profile that keeps every OKF bundle valid, moves the
strictness to where the data is, and makes a bundle describe itself instead of
depending on flags its consumers must remember to pass.

`fact` is **opinionated in what it emits and permissive in what it accepts**. It
recognizes exactly one canonical form and reports every other convention on a
graded ladder rather than rejecting it, so adopting the profile is a gradient a
bundle walks at its own pace instead of a cliff it falls off.

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
  relational content in a bundle. It is also the only content no relational
  surface can reach. Answering "what changed in March" requires re-parsing
  Markdown by hand, in a project whose entire premise is that bundle-wide
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
history lives in `changelog/`, as **55 concepts of type `Release`** — one document
per version, individually addressable, queryable, and carrying frontmatter — not
as date groups inside a `log.md`. The project reached for the concept model the
moment it needed its own history to be usable, and the spec's answer for
histories went unused in the one repository that implements the spec.

Under `fact` these documents are not special. `index` and `log` are **types**, so
a log entry is an ordinary concept with a declared timestamp: ordering becomes
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

### The bundle does not describe itself

`check --require-spec ".okf/specs/{slug}.md"` puts a fact about the bundle's own
structure in the caller's command line. Every consumer must remember to repeat
it, and the same bundle validates differently depending on who ran the command.

## Principles

Two commitments decide the arguments this RFC would otherwise have to relitigate
at every decision.

### Dogfooding

The repository that implements `fact` is a `fact` bundle, and every mechanism the
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
- **Adapters are the contribution surface.** Decision 9's "many readers, one
  writer" exists partly so that supporting a foreign dialect never requires
  touching the core or negotiating with its maintainer.
- **Type vocabularies are a public good.** URI types (decision 2) let anyone
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
magic, and no Markdown file below the bundle root is outside the model. A README
is a fact. An RFC is a fact. A type's own specification document is a fact.

This is the axiom the rest of the profile follows from, and it is what makes
decision 7 unavoidable rather than merely tidy.

It also dissolves a problem the current implementation had to build a feature
around. `README.md` explains that "a repository that keeps OKF knowledge next to
code, a README and vendored dependencies has no root that validates cleanly,"
because every unrelated Markdown file raises `OKF001`. That is a consequence of
treating some `.md` files as data and the rest as trespassers. Once every `.md`
is a fact, an unrelated file is not invalid — it is a fact whose type nobody
declared, which decision 4 reports at `warn` and never as an error.

`.factignore` therefore keeps existing, but its meaning changes: it selects
**scope**, not validity. Excluding a vendored dependency says "these facts are
not mine," not "these files are malformed." The distinction matters because the
current framing forces a choice between an unvalidatable root and unresolvable
cross-bundle links.

The cost is deliberate: `fact` has almost no parse-level errors. Unreadable
bytes, malformed YAML, and ambiguity are errors. Everything else is a level on
decision 4's ladder.

### 2. `type` prefers a URI, ideally a dereferenceable URL

Three spellings are accepted:

```yaml
type: Procedure                          # OKF-style bare string
type: fact:Procedure                     # CURIE resolved through the context
type: https://okf.dev/types/Procedure    # full URI
```

CURIE and full URI are the canonical forms `fact init` and `fact format` emit.
Every other convention stays readable; how loudly a non-canonical one is
reported is decision 4's ladder, and the bundle chooses where it sits on that
ladder.

### 3. Dereferencing is never normative

A `type` URL that 404s, times out, or is unreachable because the machine is
offline **does not affect conformance**. Dereferencing is optional enrichment,
cached under `.fact/cache/`, and no validation path may require it. Making
bundle validity depend on someone else's DNS would be a worse defect than
anything this RFC repairs.

### 4. Permissiveness is a graded ladder, not a binary

A rule that is either silent or fatal cannot express "this works, but there is a
better way." `fact` diagnostics therefore carry four levels:

| level   | meaning                                                            |
| ------- | ------------------------------------------------------------------ |
| `error` | the bundle is ambiguous or unreadable; no configuration silences it |
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
| `Procedure`, undeclared                      | `warn`  | nothing in the bundle says what it means   |
| two types sharing one local name             | `error` | ambiguity, not style                      |

The last row is the load-bearing one. Ambiguity survives maximal permissiveness
because no amount of tolerance tells a consumer which of two types a declaration
belongs to — which is why RFC 0006 already raises on a shared derived path.

Levels are per-rule and declared in `.fact/context.yaml`, so a bundle states its
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

### 5. Global identity and local name are separate

A URI identifies a type globally; it is not usable as a table name, a filename,
or a column name. RFC 0007 defines the relational table name as the exact
authored `type` value, and `CREATE TABLE "https://okf.dev/types/Procedure"` is
legal DuckDB and indefensible. Worse, truncating to the last segment reintroduces
the collision the URI was adopted to remove: `https://a.example/Task` and
`https://b.example/Task` share it.

`.fact/context.yaml` therefore carries a prefix map, and the **local name** is
the CURIE's suffix under a declared prefix. Identity is the URI; the local name
is what reaches DuckDB, paths and reports.

### 6. `.fact/` makes the bundle self-describing

```text
.fact/
  context.yaml              # prefix map; foreign vocabulary aliases; rule levels; adapters
  types/<name>.md           # the type's specification document
  types/<name>.schema.sql   # RFC 0006 declared column types
  schema.sql                # RFC 0007 bundle relational contract
  cache/                    # dereferenced remote specifications, offline-first
```

This subsumes the `--require-spec` and `--spec-template` flags: a consumer opens
the directory and discovers the arrangement rather than being told about it. The
flags remain, for compatibility and for overriding.

### 7. `.fact/` is not a magic zone

Decision 1 admits no exception for the profile's own directory. Documents under
`.fact/types/` are **ordinary facts**, discovered, parsed and queryable like any
other. The specification of a type is a fact about a type.

The axiom is that every `.md` is a fact, not that every file is Markdown.
`context.yaml`, `schema.sql` and `cache/` are sidecars, not documents, and they
carry no facts of their own — the same arrangement RFC 0006 already uses when it
derives `rotina.schema.sql` beside `rotina.md`.
Reserving `.fact/` as a region invisible to queries would reproduce exactly the
`index.md`/`log.md` mistake this RFC exists to correct.

### 8. Compatibility with foreign specifications is three mechanisms, not one

"Compatible with other specs" collapses three distinct things, and conflating
them is how a format acquires an unimplementable surface:

- **Vocabulary aliasing.** Another specification's field names map onto `fact`'s.
  `dc:title` is `title`; `schema:name` is `title`. This is pure renaming, it is
  declarative, and it belongs in `.fact/context.yaml`. Once `type` is a URI with
  a context, this mechanism is nearly free.
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
an internal architecture note to a mechanism the bundle declares.

### 9. Many readers, one writer

A format compatible with everything on both ends is unimplementable. The
asymmetry is the constraint that keeps `fact` finite: **any number of adapters may
read; exactly one canonical form is written.** Two readers disagreeing about a
foreign dialect is a bug in one adapter. Two writers disagreeing is a fork.

## Relationship to OKF v0.2

The relation is deliberately asymmetric, and calling it a superset is only
accurate in one direction:

- **OKF → `fact` is total.** Every valid OKF bundle is a valid `fact` bundle,
  unchanged, and reads with advisory diagnostics at worst. This is the sense in
  which `fact` is a superset.
- **`fact` → OKF is a lossy projection.** `fact export --okf` emits a conformant
  OKF bundle: typed `LogEntry` concepts render back into a conforming `log.md`,
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

- Whether `index` as a type survives at all, or whether a bundle-level
  description is just a concept like any other.
- Whether the `.fact/context.yaml` prefix map should adopt JSON-LD `@context`
  syntax outright rather than a lookalike.
- Which foreign vocabulary ships as a built-in alias set, if any.
