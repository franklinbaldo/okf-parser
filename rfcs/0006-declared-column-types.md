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

Scope is deliberately the *reading* half. v1 reads three things from a
declaration — column names, column types, comments — over a closed set of
types (decision 5a). Constraints are not read (decision 5), typed columns
are readable but not writable in `apply` (decision 7a), and nothing writes
back into the declaration file (deferred to RFC 0007). Each of those is a
serialization or evaluation problem that does not have to be solved for a
declared type to be useful today.

**A declared column is never the only copy of a document's data.**
Underneath every declared column sits a hidden, unconditionally lossless
raw column — `VARCHAR` for a scalar field, `VARCHAR[]` for a declared
`T[]` field — holding the field exactly as authored; the declared column
is a DuckDB *generated* column computed from it. Casting a document into a
physical type can round, truncate, or fail outright — that is inherent to
what a physical type is — but it never has to be the thing that decides
whether the original text survives. The raw column is compiler-owned:
under the `__okf_` prefix `apply` already reserves, protected the same way
`apply` already protects its other internal columns, never a target an
`--sql` script can rename or overwrite. Decision 5 is where this is built
and why two earlier drafts of the cast rule needed it.

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

**Two types can still derive the same path, and this RFC diagnoses it
rather than pretending otherwise.** `type_slug()` is deliberately not
injective — it strips accents and case — and RFC 0005 documents the exact
counterexample: `"Revisao Ciencia"` and `"Revisão Ciência"` both slug to
`revisao-ciencia`. So two distinct types can point at one `.schema.sql`.
That is a **derived-path collision**: an advisory diagnostic naming both
types and the shared file, and *neither* type gets a declaration — both
fall back to RFC 0005 compilation (decision 7's whole-declaration
fallback). Silently letting one type's declaration govern another's table
would be the one outcome worse than not typing either.

**The template must reach every surface.** `--require-spec` is currently
`check`'s alone (`cli.py`, `service.py`), so `apply`, `duckdb`, and
`schema` have no way to derive anything today. This RFC adds the same
template option, under the name `--spec-template`, to all four commands and
to their MCP equivalents; `check --require-spec` keeps its current name and
meaning (existence enforcement) and, when both are given, they must agree
or it is an operator error. A command run without a template performs no
declaration discovery at all — RFC 0005 behaviour throughout, no
diagnostics, which is what makes this opt-in at the invocation level too.

### 2. The contract is DuckDB's own SQL, executed whole and checked by post-condition

This decision superseded an earlier draft, which spent most of its length
(and most of decision 4) trying to make `.schema.sql` a **restricted,
inert data format** rather than the trusted SQL it obviously is once the
format is DuckDB DDL at all — a shape-validate-before-execute pipeline
built specifically to distinguish `CREATE TABLE` from `CREATE TABLE ... AS SELECT` by query-plan root, deny filesystem/network access on the
compiling connection, and treat `EXPLAIN`'s plan type as a security
boundary. That design was internally coherent but pointed at the wrong
goal: a file format that lets an author write arbitrary column types and
generated-column expressions, but not a join or a `read_csv`, is not safer
in any threat model worth naming — it is just less useful, for a
prohibition that inevitably chases the next construct the shape-checker
didn't anticipate. The choice this RFC actually needs is not "how do we
make `.schema.sql` safe to run" but "do we run it at all," and the answer
has to be yes: DuckDB was chosen precisely so a physical type is DuckDB's
own type, not a grammar this project re-derives. A grammar that only
accepts one `CREATE TABLE` and zero or more `COMMENT ON` statements is
already most of the way to reinventing JSON Schema with semicolons — the
exact complexity moving off JSON Schema was meant to shed.

**`.schema.sql` is trusted DuckDB SQL, full stop.** The whole file is
handed to one dedicated in-memory connection in a single `execute()` call
— CTAS, joins against other tables the script creates, `read_csv`/
`read_json`, macros, temp tables, an author's own `GENERATED ALWAYS AS`
column, anything DuckDB's parser, binder, and planner accept. Nothing in
this project inspects the script's shape, statement types, or query plan;
DuckDB already decided what the script means and in what order its
statements run. `okf-parser` checks exactly one thing afterward, against
the connection's own catalog: **the post-condition**.

```text
After the script runs:
  - exactly one non-temporary table exists whose name is the concept type
    (decision 3: table name is the type's identity);
  - every other table the script created (a staging table, a join source,
    an intermediate CTAS) is not part of the contract and is not looked
    at further.
```

A script that fails to execute, or whose catalog afterward holds zero,
two, or a differently-named table for the type, is a **malformed
declaration**: advisory diagnostic, and that type compiles exactly as if
no declaration existed (decision 7). Everything else about *how* the
table came to exist — one `CREATE TABLE`, a CTAS over a `read_csv`, a
chain of temp tables joined together — is invisible past that check.

```sql
-- All three are equally valid declarations for `Rotina`.

CREATE TABLE "Rotina" (
    id VARCHAR,
    custo DECIMAL(18, 4)
);

CREATE TABLE "Rotina" AS
SELECT id, TRY_CAST(custo AS DECIMAL(18, 4)) AS custo
FROM read_csv('rotinas.csv');

CREATE TEMP TABLE staging AS SELECT * FROM read_json_auto('rotinas/*.json');
CREATE TABLE "Rotina" AS SELECT id, valor AS custo FROM staging;
```

**An author's own generated column is preserved but unread, the same as
an authored constraint.** The compiler (decision 5) reads only a
declared column's *name* and its *type* — `duckdb_columns()` reports a
generated column's type like any other — and builds its own
raw-plus-generated pair from that. An authored `GENERATED ALWAYS AS`
expression, like an authored `CHECK` or `NOT NULL`, is simply not one of
the things v1 reads from the declaration's catalog; it is not evaluated
as part of compiling the concept table (decision 5's split, not the
declaration script's own generated-column semantics, governs what
materializes downstream).

Consequences worth stating, because they are the reason for this choice:

- **No SQL parser and no type grammar to maintain.** `DECIMAL(18, 4)`,
  whitespace, case, and every type spelling DuckDB accepts are parsed by
  DuckDB. This removes the *grammar*, not the *semantics*: decision 5a
  still names a closed set of types v1 knows how to cast and export, and a
  declaration is free to use a type outside it (the declaration stays
  well-formed; that one column simply isn't typed).
- **No new dependency.** DuckDB is already a hard dependency; nothing else
  is needed to read a declaration. (The JSON Schema alternative required
  adopting a Draft 2020-12 meta-schema validator.)
- **DuckDB's own normalized catalog is the one intermediate representation,
  not the declaration's source text.** What materializes on `apply`'s
  ephemeral table and `duckdb`'s persistent one is never the declaration
  script's `CREATE TABLE` re-run verbatim — it is a *different*
  `CREATE TABLE` the compiler synthesizes, one raw `VARCHAR`/`VARCHAR[]`
  column and one generated column per declared field, built from the name
  and type this decision's post-condition read out of the resulting
  table's catalog entry. The declaration is authoritative for *name* and
  *type*; the physical table shape is the compiler's, always. This is a
  translation with exactly one well-specified input (a `(name, type)` pair
  per column) and one well-specified output (decision 5's pair) — narrow
  enough that "cannot drift" still holds, regardless of how elaborate the
  script that produced the input was.
- **The answer to "why SQL instead of JSON Schema" gets stronger, not
  weaker.** It stops being only "DuckDB validates the type names" and
  becomes "the declaration can be a complete, executable, introspectable
  relational transformation from whatever the source data actually looks
  like to the table this type's compilation needs" — the exact thing a
  static schema format cannot express at all.

### 3. The table name is the type's identity

The `CREATE TABLE` identifier must equal the concept type the declaration's
derived path corresponds to, compared after DuckDB's own identifier
resolution (the same resolution `apply` already relies on for `--sql`).
Quoting the name is the recommended form, matching RFC 0005's tables, which
are named for the exact authored `type` value.

A declaration whose table name doesn't match is a malformed declaration
under decision 7 — not a rename, not a redirection.

### 4. Trust model: `.schema.sql` is executable code, not a sandboxed data format

A declaration file is **bundle data, not operator input** in the sense
that it usually was not typed by the person running the command — it can
come from anyone with write access to the bundle: a pull request, a
generator, a vendored bundle. Decision 2 is explicit that it is also,
simultaneously, arbitrary DuckDB SQL: joins, `read_csv`/`read_json`,
macros, whatever the connection's session accepts. Those two facts
together mean there is no honest way to also claim `.schema.sql` is safe
to run against data you don't trust. An earlier draft tried anyway —
disabling `enable_external_access`, locking configuration, distinguishing
`CREATE_TABLE` from `CREATE_TABLE_AS` by query plan before execution — and
in doing so re-imposed exactly the restricted-DDL-only shape decision 2
now rejects, for a guarantee ("this file cannot do anything but declare
columns") that was never true of a file whose whole reason for existing is
that DuckDB executes it.

**So this RFC states the trust model instead of simulating a sandbox for
it:**

> Running a bundle's `.schema.sql` grants it the same privileges as the
> `okf-parser` process itself — filesystem, network, extensions, whatever
> the DuckDB connection in that process can reach. Do not run
> `.schema.sql` from a bundle you would not otherwise trust to execute
> arbitrary code, for the same reason you would not run its Makefile,
> its migration scripts, or a notebook it ships. A bundle inside a
> repository you already build and test carries no new risk merely because
> one more of its files is `.sql`; a bundle from an untrusted upload,
> a public submission queue, or a merely-contemplative preview path (a
> site indexer, a PR diff renderer) should either not execute
> `.schema.sql` at all, or do so in whatever sandbox that caller already
> uses for untrusted code generally — that boundary belongs to the caller,
> not to this file format.

This changes which commands touch a declaration at all, and that line is
now the actual security boundary: `apply` and `duckdb` execute
`.schema.sql` (they materialize the concept table, decision 5), because a
caller running either already intends to execute code against this
bundle. `check` and `schema` also execute it today, for the same reason
`apply`/`duckdb` do — there is currently no lighter-weight way to read a
declaration's shape than running it — which is itself a reason a
merely-contemplative tool (one that only wants to *display* a bundle, not
build with it) should think about whether it wants `--spec-template` on
by default at all; that is a caller-side decision this RFC does not make
for it.

**What the compiling connection still does, and why — determinism, not
sandboxing:**

- A fresh, dedicated in-memory connection per declaration, discarded once
  the catalog has been read. This keeps one type's declaration from ever
  seeing another type's tables or a prior compilation's leftover state —
  an isolation property for *correctness* (decision 3's per-type scoping),
  not for safety.
- `SET TimeZone = 'UTC'`. Decision 5's cast rule compares timestamp
  values, and leaving the session timezone ambient would make whether a
  column types depend on the machine running the command.

Only the catalog readout — the post-condition table's column names, types
in DuckDB's normalized spelling, and comments — crosses back out. The real
compilation (`apply`'s ephemeral database, `duckdb`'s output) then issues
DDL built from that readout, never from the declaration file's text, which
is never re-executed anywhere past the one connection it ran on.

### 5. Raw is the truth; typed is a generated projection over it

> **Status: open, pending reconsideration.** This decision's own
> whole-column-degrades-to-`VARCHAR` fallback rule is in tension with the
> raw/generated split it establishes: once a lossless raw column exists
> underneath every declared column, a per-*value* `TRY_CAST` generated
> column (NULL only on the rows that actually diverge, raw text always
> intact, a diagnostic per divergent row) is available and may be the
> better rule — one bad document would then stop degrading typing for
> every other document of the same type. Recorded here rather than
> resolved now; not implemented in code either way yet (`apply`/`duckdb`
> don't compile declared types at all today - see the changelog).

Two review rounds tried to make one physical column serve two jobs at
once — carry the document's exact content, and carry a physical type —
and broke on both a numeric and a temporal counterexample each time. A
`DECIMAL(38,10)` carrier accepted `'1.00000000001'` as `BIGINT` (verified
— the carrier's own limit hid the very digit it existed to catch), and a
`TIMESTAMP`/`TIMESTAMPTZ` round-trip couldn't see a nanosecond-precision
input at all, because DuckDB's own timestamp storage is microsecond and
both sides of the comparison were truncated identically before comparing.
Every fix widened the carrier; every wider carrier had its own edge one
step further out. That is not a bug to patch again — it is what happens
when one column is asked to be both the lossless record and the physical
value.

So this RFC now keeps two, and only the compiler-facing surface changes:
a hidden raw column carrying the document's text exactly as authored, and
the declared column as a **generated column** computed from it. The raw
column's own type follows the shape of what it carries, not a single
fixed type:

```sql
-- scalar field
"__okf_raw_custo" VARCHAR,
"custo" DECIMAL(18,4) GENERATED ALWAYS AS (TRY_CAST(__okf_raw_custo AS DECIMAL(18,4)))

-- array field
"__okf_raw_tags" VARCHAR[],
"tags" BIGINT[] GENERATED ALWAYS AS (TRY_CAST(__okf_raw_tags AS BIGINT[]))
```

**`VARCHAR` for scalar fields, `VARCHAR[]` for a declared `T[]` field —
never a single "raw is always `VARCHAR`" rule**, which an earlier draft
stated and then contradicted by using `list_filter`/`list_count` (below),
both of which require an actual list argument, not text. The raw column
does not need to preserve YAML style, quoting, or indentation — that is
already `__okf_frontmatter`'s job (RFC 0005) — only the observed value at
the granularity DuckDB's own list type gives it: element order and
per-element text, with `NULL` elements representable, verified against an
empty array (`[]`) and a `NULL`-holding array both round-tripping cleanly
through this shape.

`__okf_raw_<field>` is not something a declaration writes — it is
synthesized by the compiler for every declared column, named under the
`__okf_` prefix `apply` already reserves and rejects from authored fields
(`apply.py:_check_reserved_field_names`), so a declaration or a document
using that name collides with an existing rule rather than a new one.

**That existing rule protects the *name* from authoring; it does not, by
itself, protect the *column* from a deliberate operator script — and this
RFC closes that gap explicitly rather than leaving it as an implication.**
Verified: `apply.py:_check_reserved_field_names` stops a document or a
declaration from *naming* a field `__okf_raw_custo`, but a compiled
`__okf_raw_custo` is an ordinary writable `VARCHAR` column once it
exists — `UPDATE "Rotina" SET __okf_raw_custo = '5'` succeeds today,
verified, exactly the write path the generated column above was built to
close for `custo` itself. This is not a meaningful risk under this RFC's
threat model — reaching it needs an operator deliberately writing
`--sql` naming an internal column, the same posture `apply --sql` already
trusts for everything else it executes — but it would contradict "a
declared field is read-only" and would confuse whatever RFC 0007 builds
on top of raw as the writeback target. So, extending the same reserved-
prefix protection `apply` already applies, rather than inventing a second
mechanism: every `__okf_raw_<field>` the compiler creates is added to
`apply`'s existing protected-column set (`_PROTECTED_COLUMNS`,
`apply.py:_check_result_schema`) for the duration of that run — its name,
type, and value may not be the target of `ADD`/`DROP`/`RENAME COLUMN` or
of the trailing `UPDATE`'s assignment list, the same class of rejection a
script touching `__okf_path` already gets today. It is never listed in
`field_names`, never appears in the public schema diff `apply` reports,
and is never a key `apply`'s writeback compiles into frontmatter — the
raw column is compiler-internal *state*, not a frontmatter *field*, the
same distinction RFC 0005 already draws for `__okf_body`/
`__okf_frontmatter`. It is created and dropped only in the pair its
generated column requires: when a declaration legitimately removes a
field, `apply`'s compiled `DROP COLUMN` drops both `__okf_raw_<field>`
and `<field>` together, as one internal operation — never independently.

This buys three things at once, verified rather than assumed:

- **The write guard is DuckDB's, not ours.** `UPDATE t SET custo = 5`
  against a generated column fails at bind time —
  `Binder Error: Can't update column "custo" because it is a generated column!` (verified) — which replaces the row-level Python guard the
  previous draft needed with a property the database enforces before
  `apply` ever runs its own checks. Decision 7a is now this fact, not a
  bespoke comparison.
- **No cast is ever "wrong."** `TRY_CAST` truncating a nanosecond
  timestamp to microseconds, or rounding `'1.5'` to `2`, is no longer a
  silent lie about the data — the lie was only possible when the rounded
  value *replaced* the original. Now it sits beside a raw column that
  still holds `'1.5'` exactly. What the generated column promises is
  narrower and true: "DuckDB's own `TRY_CAST` of the authored text,"
  nothing more.
- **`apply` writes only through the raw column**, per decision 7a — there
  is no serialization problem to solve for reading, because reading never
  touches raw at all.

**The all-or-nothing gate moves from "compare in a carrier" to "does the
generated column exist," decided once per column, not per row.** For each
declared column, the compiler runs one check against the whole column
before deciding whether to create it as generated:

```sql
count(*) filter (
  where v is not null and not (<exactness predicate for T> on v)
) = 0
```

If it holds for every row, the column is created generated, as above. If
it fails for any row, the column is **not** created at all — no generated
column, no typed surface — and the field is materialized exactly as RFC
0005 already does, `VARCHAR`, straight from the same text that would have
been the raw column. This is decision 5's original policy (one bad value
degrades the whole column, not just that row) preserved exactly, now
enforced before compilation instead of by discarding a cast after the
fact — and it is why a hidden raw column is unconditionally present for
every declared field regardless of outcome: it is the one thing both
branches read from.

**The exactness predicate is lexical, not carrier-based — a fixed
profile per family, with no numeric-width limit to run out of.** Verified
against DuckDB 1.5.5's `regexp_matches`, operating on the original text,
never on a cast result:

| Declared family                        | Exactness predicate on trimmed `v`                                                                                                                                                                                                                                  |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| integer (`BIGINT`)                     | `regexp_matches(v, '^[+-]?[0-9]+(\.0+)?$')`                                                                                                                                                                                                                         |
| fixed-point (`DECIMAL(p,s)`)           | `regexp_matches(v, '^[+-]?[0-9]+(\.[0-9]{0,s}0*)?$')` (`s` from the declared scale)                                                                                                                                                                                 |
| `TIMESTAMP` (naive)                    | `regexp_matches(v, '^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?$')`                                                                                                                                                                 |
| `TIMESTAMPTZ`                          | `regexp_matches(v, '^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,6})?)?(Z\|[+-][0-9]{2}(:?[0-9]{2})?)$')`                                                                                                                                   |
| `DATE`                                 | a bare `YYYY-MM-DD`, or `TIMESTAMP`'s pattern above at `00:00[:00[.0+]]` with **no** offset, or `TIMESTAMPTZ`'s pattern above at `00:00[:00[.0+]]` with offset restricted to `Z`/`+00`/`+00:00` — see below for why a non-`+00` offset is excluded even at midnight |
| `T[]`                                  | `T`'s predicate applied to every element (below)                                                                                                                                                                                                                    |
| `VARCHAR`, `BOOLEAN`, `UUID`, `DOUBLE` | `TRY_CAST` succeeding is itself exact — no lossy path from text exists for these                                                                                                                                                                                    |

Each predicate is combined with `TRY_CAST(v AS T) IS NOT NULL` — a value
can satisfy the text shape and still be out of range (`DECIMAL(3,0)` given
`'999999'`), and `TRY_CAST` catches that independently. This division of
labour is deliberate and worth stating precisely: `TRY_CAST` alone decides
*validity and range* (does this parse as a `T` at all, does it fit); the
lexical predicate alone decides *whether the chosen physical
representation would discard something the text expressed* — the two
checks answer different questions and neither substitutes for the other.

**`DATE` excludes a non-`+00` offset even at exact midnight, and this was
found by testing the cast DuckDB actually performs, not by reasoning about
what "midnight" should mean.** `TRY_CAST(v AS DATE)` does not convert
through the offset first — it is a **textual truncation** of the
date-literal component, verified: `TRY_CAST('2024-05-09T23:00:00-03' AS DATE)` returns `2024-05-09`, the literal date substring, even though that
instant is `2024-05-10 02:00:00+00` — a different UTC calendar day.
Casting the same text to `TIMESTAMPTZ` confirms it: `2024-05-10 02:00:00+00`. A naive "midnight in its own offset is safe for `DATE`"
rule would have accepted `'2024-05-09T23:00:00-03'` as an exact `DATE`
and silently materialized the wrong day. Restricting the offset-bearing
form to `Z`/`+00`/`+00:00` closes this — at UTC, DuckDB's textual
truncation and the semantically correct date coincide, and only there.

`DOUBLE` stays exact-by-`TRY_CAST`-alone deliberately: binary floating
point cannot represent most decimal fractions exactly, so a lexical
exactness rule would reject `'0.1'`, and declaring `DOUBLE` is itself the
producer's request for approximate representation. `DECIMAL(p,s)` is the
type for a value whose exactness is meant to be enforced.

Verified behaviour of the integer/decimal/timestamp rows, including the
two counterexamples the carrier-based rule missed:

```text
'1.00000000001'  -> BIGINT           rejected (was wrongly accepted by DECIMAL(38,10))
'1.00000000000'  -> BIGINT           accepted
'001'            -> BIGINT           accepted
'1.5'            -> BIGINT           rejected
'12.34'          -> DECIMAL(18,4)    accepted
'12.34567'       -> DECIMAL(18,4)    rejected
...00.123456Z    -> TIMESTAMPTZ      accepted
...00.123456789Z -> TIMESTAMPTZ      rejected (was wrongly accepted — both sides of the
                                       old round-trip truncated to the same microsecond)
```

**Arrays: element-wise, via `list_filter`/`list_count`, not
`list_reduce`.** DuckDB casts list children individually — `TRY_CAST(v AS BIGINT[])` inherits the same per-element rounding `TRY_CAST(v AS BIGINT)`
has — so `T[]`'s predicate is `T`'s predicate checked on every element,
counting violations rather than multiplying booleans:

```sql
list_count(list_filter(v, x -> x IS NOT NULL AND NOT (<predicate for T> on x))) = 0
```

An earlier draft used `list_reduce` with no initial value, which throws
outright on an empty list (verified: `Cannot perform list_reduce on an empty input list`) — a declared array field with a document holding `[]`
would have raised where it should simply pass. `list_filter`/`list_count`
was verified against the empty list (`0 = 0`, accepted) and a list holding
`NULL` (`NULL` values are skipped by the predicate, not treated as
violations, so a nullable array element is representable) with no special
case required in either statement.

**Constraints are out of v1 entirely.** `NOT NULL`, `CHECK`, `UNIQUE`,
`PRIMARY KEY`, and `DEFAULT` are a second system — `duckdb_constraints()`,
expression preservation, a per-constraint evaluation semantics over
documents, and `DEFAULT` is not even a condition existing data can
violate. v1 reads exactly three things from a declaration: **column
names, column types, and comments.** A declaration carrying constraints is
still well-formed; they are simply not read, not enforced, and not
diagnosed. They are the natural first extension once the typing half is
in use.

### 5a. The v1 type set, and what a type outside it does

Parsing accepts whatever DuckDB accepts (decision 2). *Typing* — casting a
column and exporting it — is defined for a closed set, because every
declared type has to survive two further translations that DDL parsing does
not provide: decision 5's cast predicate, and decision 9's export to JSON
Schema/Zod/Pydantic, where `schema_export.py` has an existing vocabulary of
roughly string/boolean/integer/number/date/datetime.

v1 types: `VARCHAR`, `BIGINT`, `DOUBLE`, `DECIMAL(p,s)`, `BOOLEAN`, `DATE`,
`TIMESTAMP`, `TIMESTAMPTZ`, `UUID`, and one-dimensional arrays of those.

Everything else — `HUGEINT`, `UBIGINT`, `BLOB`, `INTERVAL`, `ENUM`,
`UNION`, `MAP`, `STRUCT`, nested arrays, and any type a future DuckDB adds
— is an **unsupported-type column**. The declaration stays well-formed and
the column's comment is still applied, but the column both materializes
*and exports* exactly as if it had not been declared: RFC 0005's `VARCHAR`
in the table, `schema`'s inferred output in the export. One advisory
diagnostic names the declared type and says v1 does not type it — the
diagnostic is the only place the declared type survives.

**This is a different outcome from a failing exactness check, and the two
must not be merged.** A failing check (decision 5) means a *supported*
type the data does not currently satisfy: no generated column is created,
the field materializes as `VARCHAR` from the same raw text, but the
declared type is still exported, because it is still an intelligible
statement of intent (decision 9). An unsupported type never reaches
decision 5's check at all — there is no JSON Schema or Zod form for
`STRUCT` under decision 10's closed mapping — so exporting "the declared
type" would mean inventing one. An earlier draft routed both through a
single fallback and so said both things at once; they are now separate
cases in decision 7.

This is deliberately a soft edge: the set grows by a later RFC naming a
cast predicate and an export mapping for each addition, not by an
implementation guessing.

### 5b. Declared, undeclared, and unobserved columns

A declaration and the observed documents will not agree on the column set,
and each direction has one rule:

- **declared and observed** — typed per decision 5;
- **declared, never observed in any document** — the column exists in the
  materialized table, typed as declared, holding `NULL` in every row. A
  declaration is a statement of intent (decision 9); a field no document
  has filled in yet is exactly the case where that intent is the only
  information available. No diagnostic: not yet used is not an error;
- **observed, not declared** — the column exists, `VARCHAR`, as RFC 0005
  compiles it today, plus one advisory diagnostic per undeclared field.
  A declaration is not a closed-world schema: it never suppresses data.
  The diagnostic exists because a field nobody declared is usually either
  a typo or a schema that has moved on, and both are worth seeing.

Two names that collide under DuckDB's case-insensitive identifier equality
(`custo` declared, `Custo` observed) are the same column, and the declared
spelling is the one the table uses. Two *declared* columns colliding that
way cannot happen — DuckDB rejects the `CREATE TABLE` itself, so it lands
in decision 2's malformed-declaration path.

RFC 0005's reserved columns — the `__okf_` prefix `apply` already refuses
to let a document author use (`apply.py:_check_reserved_field_names`) —
are outside a declaration's reach: declaring one is an advisory diagnostic
and that entry is ignored. They keep the shape RFC 0005 gives them.

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
- **failing-cast fallback**, when the declaration is well-formed and the
  column's type is supported, but the data does not satisfy it (decision
  5). That one column materializes as `VARCHAR`; the declared type is
  **still exported** (decision 9), because it remains an intelligible
  statement of intent; the comment still applies; every other column is
  untouched;
- **unsupported-type fallback**, when the column's declared type is
  outside the v1 set (decision 5a). That column materializes *and exports*
  as if undeclared — inference, not declaration — because decision 10's
  mapping has no form for it. The comment still applies. The declared type
  survives only in the diagnostic.

Enumerated, the cases that reach each: whole-declaration fallback takes an
unreadable file, a script that fails to execute, a post-condition decision
2 rejects (zero, two, or a differently-named table left behind — decision
3's identity check), and a derived-path collision (decision 1, both
types). Failing-cast fallback
takes decision 5's predicate returning false for any row. Unsupported-type
fallback takes decision 5a's set, and a declared reserved column (decision
5b) behaves the same way. Undeclared observed fields and undeclared
catalog comments are diagnostics without a fallback — nothing degrades,
because nothing about them was declared.

Every case resolves to "ignore the unusable part, compile the rest exactly
as declared," never to a hard failure and never to discarding more than
the one thing that broke.

### 7a. `apply`: typed columns are read-only because they are generated columns

`apply` compiles declarations like every other surface — a declared
`TIMESTAMPTZ` column is a real `TIMESTAMPTZ` in the ephemeral table, so
`WHERE registrado_em > NOW() - INTERVAL 30 DAY` finally means what it says.
That is where most of the value is, and it is available immediately.

Two review rounds tried to enforce "typed columns aren't writable" as a
guard `apply` runs — first a schema-level check that didn't see a value
change behind a stable type, then a proposed row-level `before`/`after`
equality check to replace it. Decision 5 removes the need for either: the
declared column **is a DuckDB generated column** now, computed from the
hidden raw column, and DuckDB itself refuses to write to it:

```text
UPDATE t SET custo = 5
  -> Binder Error: Can't update column "custo" because it is a generated column!
```

verified on 1.5.5. `apply`'s trailing `UPDATE` reaches this as an ordinary
DuckDB error from `extract_statements()`/execution, the same way any other
malformed `--sql` script fails today — no new check to write, no
before/after comparison, nothing that could itself have a gap.

That protects the generated column; it says nothing about the raw column
behind it, which is an ordinary writable column until decision 5's
protected-column rule is applied — `UPDATE t SET __okf_raw_custo = '5'`
is not blocked by anything DuckDB does automatically, verified. Decision
5 covers exactly that gap by extending `apply`'s existing
`_PROTECTED_COLUMNS` set to include every `__okf_raw_<field>` for the
run's duration; a script naming one is rejected the same way one naming
`__okf_path` already is. Read-only, for a declared field, means both
columns are closed to `--sql`, not only the one DuckDB happens to guard
by itself.

**`RENAME COLUMN` on a typed column is rejected outright, not because the
guard above doesn't reach it, but because it targets the wrong column.**
`ALTER TABLE "Rotina" RENAME COLUMN custo TO valor` would rename the
generated column, not the raw one behind it — the renamed column stays
computed from `__okf_raw_custo`, which is now orphaned from the name the
caller intended to keep. `apply` rejects a `RENAME COLUMN` whose source
name is a declared column, naming the declaration. A caller who wants the
rename edits the declaration file and, until RFC 0007 gives that edit a
writeback path, removes the field from the declaration first (dropping it
to plain `VARCHAR`, which *is* the raw text and *is* renameable) before
renaming it through `apply`.

**`DROP COLUMN` on a typed column drops both.** DuckDB's own generated
column semantics mean dropping `custo` requires dropping
`__okf_raw_custo` too, or the generated column's source disappears
underneath it; `apply` drops the pair in one statement and its writeback
(RFC 0005's existing mechanism) reflects the field as removed, same as
dropping any other column today.

A declaration makes a column `apply`-readable. Making it also
`apply`-writable needs a canonical YAML serialization per type, decided
value by value — whether `12.34` becomes `12.3400` or `12.34`, how a typed
`NULL` relates to the absence/`null` distinction RFC 0005 maintains, what
a quoted-by-the-author value does — which is the same problem RFC 0007
already owns for declaration writeback. Read-only in v1 is not a
workaround for a missing guard anymore; it is what a generated column
already is.

### 7b. `duckdb`: declared types materialize in a `{schema}_types` schema, under the existing collision policy, never auto-dropped

`duckdb`'s persistent output is four tables — `concepts`, `links`,
`reserved`, `diagnostics` — and per-type tables are not among them. This
RFC does not reshape that contract: declared types materialize as one
table per type in a **second schema**, named `{schema}_types` for the
`--schema` the command was given (`okf` → `okf_types`), each table named
for the exact authored type value, quoted, as RFC 0005 names its ephemeral
tables. A separate schema, rather than the existing one, because a type
named `concepts` would otherwise collide with the contract's own table,
and because it makes the whole typed surface inspectable and droppable as
a unit.

**Two review rounds tried to invent a new ownership mechanism for this
schema — a `COMMENT ON SCHEMA` marker, then a manifest table — before
noticing `attach_okf` already has one, for the four tables it manages
today.** `_existing_tables`/`BundleExportError` (`duckdb.py`) refuse to
touch a table that already exists in the target schema unless the caller
passes `overwrite=True`, and report exactly which names collided. That is
the entire mechanism this decision needed, per table instead of
per-schema-wide-marker:

- `{schema}_types` is created if absent;
- for each currently-declared type, its table is created if absent, or
  replaced if `overwrite` is set and it already exists;
- if it already exists and `overwrite` is not set, `duckdb` raises the
  same `BundleExportError` the four contract tables already raise,
  naming the colliding table.

A forgeable marker comment, or a manifest that can itself drift from the
schema it describes, added a second source of truth for something the
codebase already decides correctly with an existence check plus an
explicit flag.

**A type's table is never dropped automatically when the type disappears
from the bundle, and the diagnostic that reports it is named
`unrecognized`, not `stale`.** This is the one place this RFC is more
conservative than an earlier draft, deliberately: distinguishing "a table
`duckdb` created and should now retire" from "a table something else put
there" requires exactly the ownership state (marker or manifest) just
rejected above as unnecessary complexity for the *write* path — and for
*deletion* the cost of getting it wrong is not symmetric with the cost of
getting it wrong on write. A table left behind costs disk and one line in
a listing; a table deleted by a wrong guess is gone. **"Stale" was the
wrong word for a table this design has no way to attribute** — without a
manifest, `duckdb` cannot tell "a type this command materialized before,
whose type has since been removed from the bundle" (genuinely stale) from
"a table a different process or a different tool put in this schema"
(never this command's to begin with); calling either one "stale" implies
a provenance claim the no-manifest design deliberately doesn't make. The
diagnostic name is corrected to match what is actually known: a table
present in `{schema}_types` whose name is not a currently-declared type is
reported as **unrecognized**, and `duckdb` does not remove it. Removing
one is a separate, explicit operation an operator invokes deliberately,
naming exactly what it will drop before it drops it; that command is not
designed here — only that automatic deletion isn't it — and is listed
under Open questions, not committed to a `--prune-stale-types` name this
decision doesn't actually specify.

**Raw columns are part of the persisted table, queryable, and not treated
as a second thing to hide or export.** `{schema}_types` is not designed
here as a stripped-down public view — it is the same table shape decision
5 already builds for `apply`'s ephemeral database, persisted. Each
declared field's hidden `__okf_raw_<field>` column sits beside its
generated column in the physical table, appears in `SELECT *`, and is
available for a consumer who wants to audit "what did the compiler
actually see" against "what the typed projection computed" — the same
comparison decision 5's design exists to make possible. The `__okf_`
prefix is the signal that it is compiler-owned and outside the declared
surface, exactly as it already signals for `__okf_path`/`__okf_body` on
the four contract tables; nothing new is being asked of that prefix here.
Two boundaries keep this from leaking further than intended: `schema --schema-format json|zod` (decision 9) never emits a raw column as a
property — its export walks the *declared* schema, and raw columns are
not declared, they are synthesized — and a declaration's `COMMENT ON COLUMN` always names the public column, never the raw one, so comments
read exactly as authored regardless of this. In `apply`, raw columns
carry the write protection decision 5 now specifies explicitly.

Types with no declaration get no table there. `{schema}_types` is the
declared surface; RFC 0005's inference is not persisted, and `concepts`
remains the complete, untyped view of everything.

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

> **Status: open, pending reconsideration.** The implemented
> `schema --spec-template` path does not currently do this: a declared
> column whose data doesn't fit falls back to inference/`string` in the
> export (advisory, matching decision 7's spirit), it does not keep
> exporting the declared type with a side diagnostic as stated below. The
> real fix is almost certainly to stop treating "schema export" as one
> mode at all and expose **declared**, **effective**, and **observed**
> as distinct, explicitly named outputs — a caller wanting the producer's
> stated intent regardless of current data, and a caller wanting what the
> data actually supports today, both have legitimate, different uses for
> `schema`, and one flag cannot serve both without lying to one of them.
> Left as prose, not implemented as decided.

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

**Presence (`required`) is inferred from the data, never from the
declaration — decision 5's constraint exclusion means there is nothing
else to base it on.** A DDL declaration carries no `NOT NULL` this RFC
reads (decision 5), so a declared column's presence follows exactly the
rule `schema_export.py` already applies to an *inferred* property: present
in `required` when every observed document carries the field, absent when
any document omits it. Declaring a column's type says nothing about
whether it must appear — that is Data Package/Table Schema's
`constraints.required`, and decision 5 deliberately left the door on
constraints closed for v1. This is the concrete answer to "does a
specified type's export become closed-world by fiat": it does not; only
type is authored, presence is still observed.

### 10. Physical types are read back in DuckDB's normalized spelling

> **Status: open, pending reconsideration.** The export table below is not
> semantically closed and does not match the implementation in at least
> two ways flagged in review: `DECIMAL(p,s)` mapping to JSON Schema
> `string` is inconsistent with JSON Schema's `number` supporting
> arbitrary precision (the implementation already exports `DECIMAL` as
> `number`, contradicting this row); and `TIMESTAMP` (no offset) and
> `TIMESTAMPTZ` cannot both honestly map to JSON Schema `format: date-time`
> (RFC 3339), which requires an offset a naive timestamp doesn't carry.
> Resolving this properly needs the current closed `CastKind` vocabulary
> (`string`/`boolean`/`integer`/`number`/`date`/`datetime`) replaced with a
> representation that preserves the full DuckDB type, not just this
> table's collapsed six-way classification — deliberately deferred to its
> own round rather than patched row-by-row here.

Every place a type is named downstream — DDL this RFC issues, diagnostics,
the `schema` export — uses the spelling DuckDB's catalog reports
(`DECIMAL(18,4)` regardless of the whitespace or case the author wrote),
never the file's original text a second time. The declaration file itself
is never rewritten by this RFC, so the author's formatting is preserved
where it lives.

The export mapping is stated exhaustively, because decision 5a's closed
type set exists precisely so that it can be:

| DuckDB         | JSON Schema                       | Zod                 |
| -------------- | --------------------------------- | ------------------- |
| `VARCHAR`      | `string`                          | `z.string()`        |
| `BIGINT`       | `integer`                         | `z.number().int()`  |
| `DOUBLE`       | `number`                          | `z.number()`        |
| `DECIMAL(p,s)` | `string`, `pattern` for the scale | `z.string()`        |
| `BOOLEAN`      | `boolean`                         | `z.boolean()`       |
| `DATE`         | `string` + `format: date`         | `z.string()`        |
| `TIMESTAMP`    | `string` + `format: date-time`    | `z.string()`        |
| `TIMESTAMPTZ`  | `string` + `format: date-time`    | `z.string()`        |
| `UUID`         | `string` + `format: uuid`         | `z.string().uuid()` |
| `T[]`          | `array` of `T`'s row above        | `z.array(...)`      |

`DECIMAL` maps to `string`, not `number`: JSON's number is a double, so
exporting a fixed-point column as `number` would hand a consumer the exact
precision loss decision 5 refuses to accept on the way in.

For everything else, the rest of the mapping follows the JSON Schema
vocabulary `schema_export.py` already emits — the exporter's
existing vocabulary, not a new one.

### 11. `init` scaffolds the missing specification documents, never a declaration

`--require-spec`/`--spec-template` (decision 1) only ever *reports* a
missing `docs/types/{slug}.md`; nothing in RFC 0005 or this RFC creates
one. A bundle adopting either rule for the first time can have dozens of
types in use and zero specification documents, and hand-creating each one
at its exact derived path is exactly the kind of bookkeeping this RFC
already refuses to make an author do by hand elsewhere (decision 1's whole
premise is that the path is *computable*, not authored).

`init <path> --spec-template TEMPLATE` computes `bundle.concept_types`,
derives each type's path the same way `check --require-spec` already does
(`type_specs.spec_relative_path`), and for every type whose document does
not yet exist, writes a minimal stub:

```markdown
---
type: Spec
---

# Rotina

TODO: describe this type's frontmatter fields and semantics.
```

Three properties keep `init` inside this RFC's existing guarantees rather
than becoming a second source of truth:

- **Never overwrites.** A document that already exists at the derived path
  is left untouched, unconditionally — `init` only fills gaps, the same
  posture `check --require-spec` already takes toward existing documents.
- **Dry-run by default, `--write` to create.** Without `--write`, `init`
  reports which paths it would create (the same shape `check --require-spec`'s diagnostics already use) and creates nothing — the
  same write-gate `apply` already uses for the same reason.
- **`--infer-schema` proposes a starter `.schema.sql`, never invents one.**
  `init` alone scaffolds only the narrative document; `--infer-schema` also
  writes a starter declaration for whichever types still lack a
  `.schema.sql`, one `CREATE TABLE` per type with every scalar field typed
  by asking DuckDB's own `TRY_CAST` — the same all-or-nothing test decision
  5 uses at *check* time, reused at *propose* time (`infer_kinds_via_duckdb`):
  a column wins the narrowest candidate (`BOOLEAN` → `BIGINT` → `DOUBLE` →
  `DATE` → `TIMESTAMPTZ`) that *every* non-null value casts into without
  losing information, falling back to `VARCHAR` when none do. "Without
  losing information" is checked literally for the two candidates where
  DuckDB's own `TRY_CAST` is lossy rather than failing —
  `TRY_CAST('10.50' AS BIGINT)` rounds to `11`, `TRY_CAST(<timestamp> AS DATE)` drops the time — by requiring the cast result to format back to
  the exact original string before it counts as a match. One `CREATE TABLE` and one bulk insert hold a type's every column at once, and one
  `SELECT` tests every column's every candidate together: a single
  vectorized DuckDB round trip, not a query per field per candidate.
  A field observed as a list or map on even one document is dropped
  entirely, the same shape restriction decision 5a already places on
  declared columns. Existing `.schema.sql` files are never overwritten,
  same as the narrative document. `--infer-schema` never turns itself on
  implicitly on a bare `init` — a starter type declaration is a bigger
  claim than a stub prose document, and stays opt-in.

### 12. `init` --require-spec derived-path collisions raise before writing anything

Decision 1 already diagnoses two types slugging to the same document as an
advisory violation `check` reports but neither the type keeps its
declaration. `init` cannot leave that decision to `check`: two types would
race to scaffold the *same* file, and whichever ran last would silently
overwrite the first type's stub content with its own — invisible under the
"never overwrites an existing file" guarantee above, since by the time the
second type is scaffolded the file already exists *because of the first
type*, not because an author wrote it. `init` therefore computes every
collision up front (the same `type_slug()` check decision 1 already runs)
and refuses to write anything — not even the non-colliding types — until
the invocation names disjoint paths, exactly the fail-closed posture
`apply --write` already takes toward a script that touched more than one
type.

### A related but out-of-scope command: `import`

This RFC's repository also ships `import SOURCE PATH --type NAME`, which
materializes every row of any DuckDB-readable source (CSV, Parquet,
(ND)JSON, via DuckDB's own replacement scan) as one concept document. It is
**not** part of this RFC and not RFC 0007's writeback: RFC 0007 is
specifically the DuckDB-catalog-back-to-frontmatter problem for a bundle
that already exists; `import` runs in the opposite direction and produces
a brand-new bundle from an external source, symmetric with the existing
`duckdb` command's bundle-to-DuckDB-tables direction. It is documented here
only because it was implemented in the same change as decisions 11-12, not
because it depends on declared column types. It follows the same dry-run/
`--write`, never-overwrite-without-`--overwrite`, fail-closed-on-duplicate-
id posture as `init` and `apply`.

## Deferred to RFC 0007: writeback

Two distinct writeback problems are deferred, and an earlier draft of this
RFC wrongly treated them as one:

- **declaration writeback** — an `ALTER TABLE` in `apply`'s script updating
  the `.schema.sql` so a shape change survives the next run;
- **typed value writeback** — an `UPDATE` assigning a non-`str` value into
  frontmatter, which decision 7a excludes from v1 by making typed columns
  read-only.

They share one unsolved problem, which is why they share an RFC: both need
a canonical serialization decided per type. Neither is a prerequisite for
declared types being useful, since reading and filtering by real types is
where the value is.

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

JSON Schema remains a first-class **output** — `schema --schema-format json` is unchanged and now reflects declarations (decision 9). Using it as
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
is a second fact free to disagree with the first, and it ties a type's
identity to bundle layout. It does **not** avoid collisions — deriving
does not either, since `type_slug()` is not injective — so the comparison
is between two designs that both need a collision diagnostic, and deriving
is the one that keeps `type` the single source of identity (decision 1).

### Semantic discovery (scanning the bundle for a document declaring the exact type)

Rejected for v1, though it is the honest alternative to decision 1's
collision diagnostic, since matching an exact `type` value avoids slug
collapse entirely. It costs a bundle-wide scan on every command, and it
reintroduces the ownership-conflict and identity-ambiguity cases as things
to detect and diagnose rather than as one path comparison. If derived-path
collisions turn out to be common in practice rather than pathological,
this is the change to make.

### Rejecting the bundle when a value doesn't match its declared type

Rejected. A declaration is an expectation the data may not have caught up
with; making it a hard failure would mean a producer cannot declare an
intended type until the data is already perfect, which is exactly backwards
— the declaration is how they find out it isn't. `--fail-on-spec-divergence`
(decision 8) gives the strict posture to whoever wants it, opt-in.

### Restricting `.schema.sql` to a single declarative `CREATE TABLE`, sandboxed by configuration

An earlier draft of decisions 2 and 4 rejected `CREATE TABLE ... AS SELECT` and every other multi-statement shape, distinguishing it from
plain `CREATE TABLE` by `EXPLAIN`'s query-plan root (`CREATE_TABLE` vs.
`CREATE_TABLE_AS`), and ran the surviving single statement on a connection
with `enable_external_access=false` and `lock_configuration=true` — an
attempt to make `.schema.sql` a restricted, safe-by-construction data
format despite being literal DuckDB SQL.

Rejected in favor of decision 2/4's trust-model statement, for two
reasons. First, it does not actually deliver a security property: nothing
stops the *next* accepted construct (a scalar subquery, a table function,
a future DuckDB feature) from doing exactly what CTAS was singled out for,
so the shape-checker's job is never finished, only extended reactively.
Second, and more importantly, it works directly against this RFC's own
stated reason for choosing DuckDB DDL over JSON Schema in the first place
— "DuckDB parses the file; its catalog is the parsed representation" only
buys freedom from maintaining a grammar if the grammar stays DuckDB's
whole grammar. A declaration format that accepts column types but not a
join is a bespoke, JSON-Schema-shaped subset of SQL wearing a `.sql`
extension, carrying the exact maintenance burden ("what constructs do we
accept, and how do we detect the ones we don't") the migration away from
JSON Schema was meant to shed. The post-condition model (decision 2) gets
the real benefit — an author can express any relational transformation
DuckDB supports — for less implementation surface, not more, because it
checks one fact about the result instead of policing every way of
reaching it.

### `duckdb` as an atomically-published build artifact, not a schema written into a caller's connection

Considered as the structural fix to decision 7b's ownership question: if
`duckdb` compiled to a fresh file and swapped it into place atomically,
there would be no existing schema to collide with, ever, and no orphan
question either. Rejected for *this* RFC, not on merits — `attach_okf`'s
existing contract takes a connection the caller already opened
(`duckdb.py:attach_okf`), not a file path it owns end to end; changing
that is a reshaping of RFC 0005's persistence model, which this RFC does
not otherwise touch anywhere else. It is a stronger design for `duckdb`
in general and belongs in its own RFC, decoupled from whether declared
types exist at all.

### Content-addressed generations for `{schema}_types` (`okf_types_<hash>` plus a stable pointer)

Considered alongside the above: every compilation writes a new,
independently named schema; a successful one repoints a stable alias;
nothing already published is ever mutated in place. Rejected for v1 for
the same reason the marker and the manifest were: it is real machinery
(hashing, pointer indirection, generation garbage collection) motivated by
a failure mode — concurrent or partial writes corrupting a shared schema
— that the simpler create-or-replace-with-`overwrite`, refuse-on-unknown
policy (decision 7b) already handles for the common case of one operator
running `duckdb` against their own database. Worth revisiting if
`duckdb` grows a concurrent or CI-shared-database use case this RFC
doesn't target.

## Open questions

- Exact `OKF0xx` codes for decision 7's cases — the existing numbering runs
  through `OKF010`, and the assignment should be made against
  `bundle.py`/`type_specs.py` rather than fixed here in isolation.
- Whether `{schema}_types` (decision 7b) should hold tables or views. This
  RFC says tables, matching the four contract tables; views would avoid the
  copy but need the source relation to be queryable from the persisted
  database, which the current output does not guarantee.
- The explicit, separate deletion command for `{schema}_types` tables
  whose type no longer exists in the bundle (decision 7b calls the state
  `unrecognized` and specifies that automatic deletion isn't the answer;
  the command itself, its name, and its confirmation shape are not
  designed here).
- In-artifact comment provenance (decision 6) if git history ever proves
  insufficient.
- Constraints (decision 5, excluded from v1): whether a later RFC reads
  `duckdb_constraints()` for `NOT NULL`/`CHECK`/`UNIQUE`/`PRIMARY KEY`, and
  what evaluating each against documents means. `DEFAULT` likely never
  belongs, being a write-time concept with no read-time claim.
- Extending decision 5a's type set — each addition needs both a cast
  predicate (decision 5) and an export mapping (decision 10), which is the
  deliberate cost of adding one.
- Whether the conformance suite needs shared Python/TypeScript fixtures for
  declaration compilation, once a TypeScript implementation is in scope.

This RFC depends on RFC 0005, accepted and implemented (#30, #32), for the
relational compilation it extends.
