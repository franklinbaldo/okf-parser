---
type: RFC
title: Declared column types for concept tables
status: proposed
description: Let an optional DuckDB DDL file sitting beside a type's specification document declare that type's physical column types and catalog comments, compiled into every relational surface, advisory in every direction and never a block
---

# RFC 0006: Declared column types for concept tables

## Summary

RFC 0005 compiles each concept type into a DuckDB table by inferring every
column as `VARCHAR`. Inference is practical — it needs no author effort and
is always available — but it can only describe what the data currently is,
never what it is *supposed* to be. There is no place for a producer to say
"`registrado_em` is a `TIMESTAMPTZ`" and no mechanism that notices when the
data stops agreeing.

This RFC adds an **optional** second compilation input. If a type's
specification document has a DDL file beside it, that file declares the
type's physical column types and its catalog comments, and every relational
surface (`apply`, `duckdb`, `schema`) compiles them instead of inferring.
The file is plain DuckDB SQL:

```sql
CREATE TABLE "Rotina" (
    id            VARCHAR,
    registrado_em TIMESTAMPTZ,
    execucoes     BIGINT,
    ativa         BOOLEAN,
    custo         DECIMAL(18, 4)
);

COMMENT ON TABLE "Rotina" IS 'Rotina administrativa executada pelo setor.';
COMMENT ON COLUMN "Rotina".registrado_em IS 'Momento em que a rotina foi registrada.';
```

Nothing here is required. A type with no DDL file compiles exactly as RFC
0005 already defines — every column `VARCHAR`, no comments — unchanged.
Declaration is opt-in, per type, and its absence is not a defect.

Declaration buys **validation**, not obedience: a declared type that the
data doesn't support degrades that one column back to `VARCHAR` and reports
it. The bundle still opens, the table still builds, the export still
emits. Divergence is advisory by default and changes only the exit code
when the caller explicitly asks it to (decision 8).

## Motivation

`check --require-spec` (0.14.0) established that a type's specification
document is worth having and worth deriving a path for. It stops at
*existence*: the rule is "does a document exist at this path," never "does
this document's account of the type hold." RFC 0005 then made types
relational, but with a single physical type for everything.

Three things are missing at once, and one file supplies all three:

- **Physical types.** `WHERE registrado_em > '2024-01-01'` against a
  `VARCHAR` column is a lexicographic comparison that happens to work for
  ISO-8601 and silently misbehaves for anything else. Declared `TIMESTAMPTZ`
  makes it a real comparison.
- **Drift detection.** A declared type is a checkable claim. When a document
  starts carrying `"n/a"` in an integer field, something should say so —
  today nothing can, because nothing was ever claimed.
- **Documentation in the catalog.** The prose describing a field lives in a
  Markdown body that no relational consumer reads. `COMMENT ON` puts it
  where a SQL client will actually show it.

## Relationship to RFC 0005

Purely additive. RFC 0005's compilation is the fallback path and stays the
behaviour for every type that does not opt in, and the behaviour any
single column falls back *to* when its declaration doesn't hold. This RFC
adds no new command and no new required field.

## Decision

### 1. The declaration is a DuckDB DDL file at a derived path

`type_specs.py` derives a specification document path from the type name
through the `--require-spec` template (`{slug}`). This RFC derives the
declaration path from that same document by replacing its extension:

```text
docs/types/rotina.md          # narrative documentation (existing, --require-spec)
docs/types/rotina.schema.sql  # physical declaration (new, optional)
```

The path is **derived, never declared**, for the reason `type_specs.py`
already states in its own module docstring: a `schema:` frontmatter field
would be a second fact free to disagree with the first, and would tie a
type's identity to bundle layout. Deriving keeps `type` the sole identity
and makes the declaration's location computable from it. A type with a
specification document and no `.schema.sql` beside it is a type that did
not opt in — not a diagnostic.

This also disposes of an entire category the declared-path design had to
handle: two documents cannot claim the same declaration file, and a
declaration file cannot claim a type other than the one whose path derived
it, so ownership conflicts and identity ambiguity are unrepresentable
rather than diagnosed.

### 2. The contract is DuckDB's own DDL, parsed by DuckDB

The file's grammar is exactly one `CREATE TABLE` statement, followed by
zero or more `COMMENT ON TABLE`/`COMMENT ON COLUMN` statements. Nothing
else — no `INSERT`, no `ATTACH`, no second table, no pragma.

`okf-parser` does not parse SQL. It hands the file to DuckDB's
`extract_statements()` (already used by `apply` for `--sql`) to split it,
validates the *shape* of the resulting statement list, and executes it
against a throwaway in-memory database that has no attached files and no
data. The compiled result is read back from `duckdb_columns()` and
`duckdb_tables()` — DuckDB's own catalog is the parsed representation.
Both catalog views carry a `comment` column, so a declaration's comments
survive the round trip through the catalog exactly like its types do; no
side table is needed to hold them.

One implementation constraint, verified against DuckDB 1.5.5 rather than
assumed: `extract_statements()` reports `COMMENT ON` with
`StatementType.ALTER`, the same value it reports for `ALTER TABLE`. Shape
validation therefore cannot rest on the statement type alone — it must
confirm the first statement is `StatementType.CREATE` and that every
subsequent statement, after execution, produced only comment changes and
no schema change (comparable by reading `duckdb_columns()` before and
after). Stating it here so an implementation does not discover it as a
hole in the shape check.

Consequences worth stating, because they are the reason for this choice:

- **No SQL parser and no type grammar to maintain.** `DECIMAL(18, 4)`,
  `STRUCT`, `MAP`, whitespace, case, and every future DuckDB type work
  because DuckDB parses them. There is no mapping table to keep in sync
  with DuckDB releases and no list of "types the profile supports."
- **No new dependency.** DuckDB is already a hard dependency; nothing else
  is needed to read a declaration. (The JSON Schema alternative required
  adopting a Draft 2020-12 meta-schema validator.)
- **The declaration is the DDL that gets run.** There is no translation
  step between what the author wrote and what the table becomes, so the
  two cannot drift.

A file that fails this shape validation — a second statement type, a
missing `CREATE TABLE`, a syntax error DuckDB rejects — is a **malformed
declaration**: advisory diagnostic, and that type compiles exactly as if
no declaration existed (decision 7).

### 3. The table name is the type's identity

The `CREATE TABLE` identifier must equal the concept type the declaration's
derived path corresponds to, compared after DuckDB's own identifier
resolution (the same resolution `apply` already relies on for `--sql`).
Quoting the name is the recommended form, matching RFC 0005's tables, which
are named for the exact authored `type` value.

A declaration whose table name doesn't match is a malformed declaration
under decision 7 — not a rename, not a redirection.

### 4. Execution is isolated and side-effect-free

A declaration file is **bundle data, not operator input**. Unlike `--sql`,
which the caller typed, a `.schema.sql` can come from anyone with write
access to the bundle. It is therefore never concatenated into a larger
script and never run against a database holding data or attached files:

- statements are split with `extract_statements()` before anything runs, so
  a statement separator smuggled into an identifier or a type expression
  cannot extend the script;
- the resulting statements run against a fresh in-memory database created
  for this purpose alone, discarded once the catalog has been read;
- only the catalog readout — column names, types in DuckDB's normalized
  spelling, comments — crosses back out. The statement text itself is never
  re-executed anywhere else.

The real compilation (`apply`'s ephemeral database, `duckdb`'s output) then
issues DDL built from that catalog readout, never from the file's text.

### 5. Casting is all-or-nothing, per column

For each declared column, the compiler casts the whole column with
`TRY_CAST`. If **every** non-null value converts, the column materializes
with its declared type. If **any** non-null value yields `NULL`, the
**entire column** falls back to `VARCHAR` — never partially typed, never
typed with the offending rows nulled out.

The threshold is deliberately absolute rather than proportional: a column
whose type depends on the ratio of bad values is a column whose type
changes when a document is added, which is worse for a consumer than a
column that is simply `VARCHAR` until the data is clean. One bad value in
ten thousand degrades the column, and the diagnostic says exactly that —
declared type, physical type used, failure count, first offending value and
the document holding it.

Casting is **strict, never lossy**. `TRY_CAST` is the whole rule: a
conversion DuckDB will not perform without an explicit cast is a failure,
not something to coerce. A `date-time`-shaped value declared as `DATE` is
not truncated to make it fit — it degrades the column and is reported. This
settles what "convertible" means without inventing a second cast semantics
beside DuckDB's.

`NOT NULL` in a declaration is **recorded and diagnosed, never enforced**.
Emitting a real `NOT NULL` constraint would turn an advisory declaration
into a physical barrier that blocks materializing the very rows the
diagnostic exists to describe. A declared-`NOT NULL` column holding a null
is a diagnostic; the row still materializes. The same holds for `PRIMARY
KEY`, `UNIQUE`, `CHECK`, and `DEFAULT`: preserved in the declaration,
reported when violated, never compiled into a constraint.

### 6. Comments: the declaration is canonical, existing comments are never destroyed

`COMMENT ON TABLE`/`COMMENT ON COLUMN` in the declaration are re-issued
against every table this RFC materializes — `apply`'s ephemeral table and
`duckdb`'s output alike.

Two rules govern what happens to comments already present in a persistent
target:

- a comment the declaration **does** specify is overwritten by the
  declaration's text: the file is canonical, and a materialization is a
  recompilation, not a merge;
- a comment the declaration **does not** specify, on a table or column that
  already carries one, is **preserved, never dropped**, and reported as an
  advisory diagnostic ("catalog comment not declared"). Prose that exists
  outside the declaration is information; silently deleting it on the next
  run would be the worst possible handling of it.

Comments do **not** enter `apply`'s `--sql` grammar. RFC 0005's script
shape (leading `ALTER TABLE`s, one trailing `UPDATE`) is unchanged; a
comment is edited by editing the declaration file, which is no heavier.

**Comment history is explicitly out of scope.** The declaration is a
version-controlled file, so git already holds every previous wording with
its author and date; encoding a second history inside the artifact would
duplicate that badly. If a need for in-artifact provenance emerges, it is
its own RFC (see Open questions).

### 7. Divergence is advisory, and "advisory" names a defined fallback

No declaration failure and no data divergence ever rejects a bundle,
blocks a table, or suppresses an export. Saying so is not enough on its
own — what actually compiles when a declaration is broken has to be
settled, or two implementations can legitimately disagree. Two degrees of
fallback, and only two:

- **whole-declaration fallback**, when nothing about the file's content can
  be localized: unreadable file, a statement shape decision 2 rejects, a
  DuckDB syntax error, or a table name that doesn't match (decision 3).
  The type compiles **exactly as it would with no declaration at all** —
  RFC 0005's `VARCHAR` inference in `apply`/`duckdb`, `schema`'s existing
  inferred/cast output — and one diagnostic names the file and the reason.
- **single-column fallback**, when the declaration is well-formed but one
  column's declared type does not hold against the data (decision 5). That
  one column materializes as `VARCHAR`; every other column keeps its
  declared type; the column's comment and its declared type are still
  reported and still exported (decision 9). Nothing about the rest of the
  declaration is in question.

This is exhaustive for v1: every case resolves to "ignore the unusable
part, compile the rest exactly as declared," never to a hard failure and
never to discarding more than the one thing that broke.

### 8. `--fail-on-spec-divergence` changes the exit code and nothing else

Advisory by default is right for authoring and wrong for CI, where a
divergence nobody reads is a divergence that stays. Every command that can
report these diagnostics — `check`, `apply`, `duckdb`, `schema` — accepts:

```text
--fail-on-spec-divergence
```

It changes **only the process exit code** (non-zero when at least one
declaration diagnostic was reported). It does not change which diagnostics
are produced, does not change their text, does not change the artifact, and
does not change any output stream — `schema --schema-format zod` still
writes exactly the Zod source to stdout and is still redirectable to a
`.ts` file. Default: off, exit code unaffected.

One flag across all four surfaces, rather than per-surface escalation: the
question a CI job asks ("did anything diverge?") is the same question
regardless of which command it happens to be running. `check`'s existing
`--normative-spec` is untouched and keeps governing `--require-spec`'s own
existence rule.

The environment is never sniffed to decide this. A command behaves the same
in CI as on a laptop unless the flag is passed.

### 9. `schema` exports the declaration, not a measurement of the data

For a type with a declaration, `schema` emits the **declared** types —
`--infer-types` is not consulted for a declared column, and a `--cast`
naming a declared column is a diagnostic ("cast conflicts with declared
schema") that is reported and not applied.

This holds **even when the data does not currently satisfy the
declaration**. The exported schema is a statement of intent — what the
producer says the type is — and that is precisely the useful thing to hand
a downstream consumer; a schema that silently widens to `string` every time
one document is malformed would communicate nothing and change shape
without anyone deciding it should. The declared type is exported, the
column materializes as `VARCHAR` per decision 5, and the divergence appears
as a diagnostic. Consumers are told, in the artifact:

- **`--schema-format json`** gains a `"diagnostics"` key in the dict
  `export_json_schema` already returns, in the same shape `check`'s
  `diagnostics` array uses (empty list when there is nothing to report),
  alongside the existing `root`/`total_types`/`inferred_types`/`casts`/
  `schemas` keys;
- **`--schema-format zod`** keeps returning a bare Zod source string —
  stdout is the artifact — and writes diagnostics to **stderr**, one
  human-readable line each;
- **`build_pydantic_models()`** is a library API with a stable
  `dict[str, type[BaseModel]]` return type, unchanged here; a caller
  needing diagnostics calls the JSON mode against the same bundle.

Types with no declaration keep `--infer-types` and `--cast` behaviour
exactly as it is today.

### 10. Physical types are read back in DuckDB's normalized spelling

Every place a type is named downstream — DDL this RFC issues, diagnostics,
the `schema` export — uses the spelling DuckDB's catalog reports
(`DECIMAL(18,4)` regardless of the whitespace or case the author wrote),
never the file's original text a second time. The declaration file itself
is never rewritten by this RFC, so the author's formatting is preserved
where it lives.

For `schema`'s JSON/Zod output, DuckDB types map to the JSON Schema
vocabulary `schema_export.py` already emits (`BIGINT` → `integer`,
`TIMESTAMPTZ` → `string`/`format: date-time`, and so on) — the exporter's
existing vocabulary, not a new one.

## Deferred to RFC 0007: writeback

An earlier draft of this RFC also specified the reverse direction: an
`ALTER TABLE` in `apply`'s script writing back into the declaration file so
a schema change survives the next run. That is a second system, it carries
every remaining hard question, and nothing in this RFC depends on it. It is
deferred whole.

The specific problem it has to solve, stated here so 0007 starts from it:
regenerating a declaration file from a DuckDB catalog produces *canonical*
DDL, discarding the author's whitespace and SQL comments — **and, verified
against DuckDB 1.5.5, dropping the `COMMENT ON` statements entirely**.
`duckdb_tables().sql` for a table declared with comments returns

```sql
CREATE TABLE Rotina(id VARCHAR, registrado_em TIMESTAMP WITH TIME ZONE);
```

with no comment statements and no quoting of the identifier, even though
`duckdb_tables().comment` still holds the prose. So a naive
"regenerate from `.sql`" writeback would silently delete every comment in
the declaration file — the exact outcome decision 6 forbids. A correct
writeback must reassemble the `COMMENT ON` statements from the catalog's
`comment` columns itself, or edit the file surgically, which needs a real
SQL parser that decision 2 deliberately avoids. Resolving that tension is
0007's job, and it is the concrete reason writeback is not v1.

Until then, `apply`'s `ALTER TABLE` behaves exactly as RFC 0005 defines:
it reshapes the ephemeral table only. A caller who wants the change to
persist edits the declaration file, which is the same file they would have
been reviewing in the diff anyway.

## Alternatives considered

### JSON Schema Draft 2020-12 as the contract (the previous draft of this RFC)

Rejected, after being the design for several revisions. JSON Schema is the
better-known standard and has richer semantics (`required`, `format`,
`enum`, composition), but for *this* job it loses on every axis that
matters:

- it cannot express a physical DuckDB type at all, so it needed an
  `x-okf-duckdb-type` extension keyword — meaning the real contract was
  DuckDB type strings anyway, wrapped in a format that couldn't validate
  them;
- it needed a mapping table from JSON Schema types to DuckDB types, a v1
  profile listing which keywords are interpreted, and a rule for properties
  the profile can describe but not materialize — three sources of
  divergence that DDL simply doesn't have;
- it required adopting a Draft 2020-12 meta-schema validation library as a
  new runtime dependency, to validate a document DuckDB would then have to
  re-interpret;
- it has no standing in the DuckDB ecosystem: DuckDB expresses schemas as
  SQL (`EXPORT DATABASE` writes `schema.sql`, `duckdb_tables().sql` returns
  DDL, `DESCRIBE` reports types), and the community `json_schema` extension
  validates JSON *documents*, never table shapes.

JSON Schema remains a first-class **output** — `schema --schema-format
json` is unchanged and now reflects declarations (decision 9). Using it as
the input as well conflated a serialization format with a type system.

### A bundle-invented `fields: {name: {type, description}}` frontmatter shape

Rejected. A schema language with exactly one implementation, no external
tooling, and a grammar this project would have to grow indefinitely.

### Embedding the DDL in the specification document's frontmatter

Rejected. YAML-escaped SQL is unreadable and uneditable, no editor
highlights or checks it, and the document's body would sit between the
reader and the contract. A `.sql` file is opened by any tool that
understands SQL.

### A declared `schema:` path in frontmatter instead of a derived one

Rejected, for the reason `type_specs.py` already documents: a declared path
is a second fact free to disagree with the first, and it makes two
documents able to claim the same declaration. Deriving makes the conflict
unrepresentable (decision 1).

### Rejecting the bundle when a value doesn't match its declared type

Rejected. A declaration is an expectation the data may not have caught up
with; making it a hard failure would mean a producer cannot declare an
intended type until the data is already perfect, which is exactly backwards
— the declaration is how they find out it isn't. `--fail-on-spec-divergence`
(decision 8) gives the strict posture to whoever wants it, opt-in.

## Open questions

- Exact `OKF0xx` codes for decision 7's cases — the existing numbering runs
  through `OKF010`, and the assignment should be made against
  `bundle.py`/`type_specs.py` rather than fixed here in isolation.
- Whether `duckdb`'s persistent output should materialize declared types
  into physical tables or expose them as views is a materialization
  question RFC 0005 left open and this RFC does not settle.
- In-artifact comment provenance (decision 6) if git history ever proves
  insufficient.
- Whether `CHECK`/`UNIQUE`/`PRIMARY KEY` divergence deserves its own
  diagnostic codes distinct from type divergence, or one code covering all
  unenforced constraints.
- Whether the conformance suite needs shared Python/TypeScript fixtures for
  declaration compilation, once a TypeScript implementation is in scope.

This RFC depends on RFC 0005, accepted and implemented (#30, #32), for the
relational compilation it extends.
