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

`fact` is **opinionated in what it emits and permissive in what it accepts**.
Every rule below that tightens something is advisory by default and promotable
to normative by an explicit flag, following the precedent `--require-spec` and
`--normative-spec` already set in this project.

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

## Decision

### 1. `type` prefers a URI, ideally a dereferenceable URL

Three spellings are accepted:

```yaml
type: Procedure                          # OKF-style bare string
type: fact:Procedure                     # CURIE resolved through the context
type: https://okf.dev/types/Procedure    # full URI
```

A bare string is accepted with an advisory diagnostic and is never an error.
CURIE and full URI are the canonical forms `fact init` and `fact format` emit.
`--normative-uri` promotes the advisory to an error for a bundle that has
finished adopting.

### 2. Dereferencing is never normative

A `type` URL that 404s, times out, or is unreachable because the machine is
offline **does not affect conformance**. Dereferencing is optional enrichment,
cached under `.fact/cache/`, and no validation path may require it. Making
bundle validity depend on someone else's DNS would be a worse defect than
anything this RFC repairs.

### 3. Global identity and local name are separate

A URI identifies a type globally; it is not usable as a table name, a filename,
or a column name. RFC 0007 defines the relational table name as the exact
authored `type` value, and `CREATE TABLE "https://okf.dev/types/Procedure"` is
legal DuckDB and indefensible. Worse, truncating to the last segment reintroduces
the collision the URI was adopted to remove: `https://a.example/Task` and
`https://b.example/Task` share it.

`.fact/context.yaml` therefore carries a prefix map, and the **local name** is
the CURIE's suffix under a declared prefix. Identity is the URI; the local name
is what reaches DuckDB, paths and reports.

### 4. `.fact/` makes the bundle self-describing

```text
.fact/
  context.yaml              # prefix map; foreign vocabulary aliases; declared adapters
  types/<name>.md           # the type's specification document
  types/<name>.schema.sql   # RFC 0006 declared column types
  schema.sql                # RFC 0007 bundle relational contract
  cache/                    # dereferenced remote specifications, offline-first
```

This subsumes the `--require-spec` and `--spec-template` flags: a consumer opens
the directory and discovers the arrangement rather than being told about it. The
flags remain, for compatibility and for overriding.

### 5. `.fact/` is not a magic zone

Documents under `.fact/types/` are **ordinary concepts**, discovered, parsed and
queryable like any other. The specification of a type is a fact about a type.
Reserving `.fact/` as a region invisible to queries would reproduce exactly the
`index.md`/`log.md` mistake this RFC exists to correct.

### 6. Compatibility with foreign specifications is three mechanisms, not one

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

### 7. Many readers, one writer

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
