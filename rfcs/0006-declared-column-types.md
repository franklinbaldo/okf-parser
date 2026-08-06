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

### 2. The contract is DuckDB's own DDL, parsed by DuckDB

The file's grammar is exactly one **declarative** `CREATE TABLE` — the
`CREATE TABLE name (column type, ...)` form and nothing else — followed by
zero or more `COMMENT ON TABLE`/`COMMENT ON COLUMN` statements. No
`INSERT`, no `ATTACH`, no second table, no pragma, and specifically **no
`CREATE TABLE ... AS SELECT`**, no `CREATE OR REPLACE`, no `CREATE TABLE
... AS` of any shape, no generated columns.

CTAS deserves being named rather than left to "no `SELECT`", because it is
the one form that looks exactly like what this decision wants and is not:

```sql
CREATE TABLE "Rotina" AS FROM read_csv('/etc/passwd');
```

`extract_statements()` reports that as `StatementType.CREATE` (verified),
it produces a table with the expected name, and a catalog readout after it
would return plausible columns. Neither the statement type nor anything
about the resulting table distinguishes it. Decision 4 states how it is
rejected — **before execution**, which is the only point at which
rejecting it is worth anything.

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
  whitespace, case, and every type spelling DuckDB accepts are parsed by
  DuckDB. This removes the *grammar*, not the *semantics*: decision 5a
  still names a closed set of types v1 knows how to cast and export, and a
  declaration is free to use a type outside it (the declaration stays
  well-formed; that one column simply isn't typed).
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

### 4. Execution is isolated, and isolation is configured rather than assumed

A declaration file is **bundle data, not operator input**. Unlike `--sql`,
which the caller typed, a `.schema.sql` can come from anyone with write
access to the bundle — a pull request, a generator, a vendored bundle. An
in-memory database is *not* by itself a sandbox: `:memory:` still reaches
the filesystem, the network, and the extension loader, because
`enable_external_access` defaults to true. Isolation has to be configured,
and this decision configures it.

The connection a declaration is compiled on is created for that purpose
alone, discarded once the catalog has been read, and set up as:

```sql
SET enable_external_access = false;
SET lock_configuration = true;
```

`enable_external_access=false` denies file, HTTP, and attach access;
`lock_configuration=true` makes the setting unresettable, so a `SET` inside
the declaration cannot undo it. Verified: with it, `CREATE TABLE r AS FROM
read_csv('/etc/hostname')` fails with `Permission Error: ... file system
operations are disabled by configuration`.

That is the second barrier. The first is decision 2's shape rule, enforced
in three steps:

- statements are split with `extract_statements()` **before** anything
  runs, so a separator smuggled into an identifier or a type expression
  cannot extend the script;
- the first statement must be `StatementType.CREATE`, **and its query plan
  root must be `CREATE_TABLE`, not `CREATE_TABLE_AS`.** DuckDB's own
  planner makes the distinction the statement type does not, and `EXPLAIN`
  exposes it without executing anything (verified on 1.5.5):

  ```text
  EXPLAIN CREATE TABLE t(a INT, b VARCHAR)         -> CREATE_TABLE
  EXPLAIN CREATE TABLE t AS FROM src WITH NO DATA  -> CREATE_TABLE_AS
  EXPLAIN CREATE TABLE t AS FROM src LIMIT 0       -> CREATE_TABLE_AS
  ```

  This is the check an earlier draft got wrong. It proposed counting rows
  after execution instead, on the claim that "every CTAS produces rows or
  fails" — false: `WITH NO DATA` and `LIMIT 0` are CTAS forms whose entire
  purpose is producing an empty table, and both passed that check
  (verified). Planning rather than counting also means the CTAS never runs
  at all, which is what closes the resource-exhaustion shape
  (`CREATE TABLE t AS FROM range(1e12) WHERE ...`) properly: a rejected
  plan does no work, whereas a row count is taken after the work is done;
- every subsequent statement must, after execution, have changed only
  comments and not the column set (comparable by reading
  `duckdb_columns()` before and after), which is what closes the
  `COMMENT ON`-reports-as-`StatementType.ALTER` hole named in decision 2.

A zero-rows assertion on the created table is kept as defence in depth —
it costs nothing and would catch a future `CREATE_TABLE`-planned form that
somehow carries data — but it is explicitly **not** the proof of shape.

**Two forms this RFC no longer claims to reject**, because claiming it
without a mechanism was the same error in miniature:

- `CREATE OR REPLACE TABLE` plans as `CREATE_TABLE` and is not
  distinguishable there. It is also harmless: the connection is fresh and
  empty, so there is nothing for it to replace. It is accepted, and
  compiles identically to `CREATE TABLE`.
- A **generated column** (`b INT GENERATED ALWAYS AS (a*2)`) also plans as
  `CREATE_TABLE`, and — verified — leaves `information_schema.columns`'s
  `is_generated`/`generation_expression` empty, so the catalog does not
  reveal it either. It is accepted: the table has no rows, so the
  expression is never evaluated, and the column's declared type is in the
  catalog like any other. A declaration using one is compiled from its
  catalog type, and the generation expression is simply not carried
  anywhere.

Only the catalog readout — column names, types in DuckDB's normalized
spelling, comments — crosses back out. The real compilation (`apply`'s
ephemeral database, `duckdb`'s output) then issues DDL built from that
readout, never from the file's text, which is never re-executed anywhere.

The compiling connection also pins `SET TimeZone = 'UTC'`. Decision 5's
cast rule compares timestamp values, and leaving the session timezone
ambient would make whether a column types depend on the machine running the
command.

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

**`TRY_CAST` alone is not enough, and an earlier draft of this decision was
wrong to say it was.** `TRY_CAST` nulls a conversion that *fails*; it says
nothing about a conversion that *succeeds while losing information*.
Verified against DuckDB 1.5.5:

```text
TRY_CAST('1.5' AS INTEGER)                      -> 2          rounds
TRY_CAST('12.34567' AS DECIMAL(18,4))           -> 12.3457    rounds
TRY_CAST('2024-05-10T14:30:00Z' AS DATE)        -> 2024-05-10 truncates
TRY_CAST('2024-05-10 14:30:00+03' AS TIMESTAMP) -> 14:30:00   drops the offset
```

Every one of those is a silent lie about the data. So the rule has a
second half: **exact-carrier comparison**. A value casts cleanly when
`TRY_CAST` succeeds *and* the typed value still equals the original when
both are compared in a carrier that is exact for the declared type's
family:

| Declared family | Accepted when |
| --- | --- |
| integer (`BIGINT`) | `TRY_CAST(v AS DECIMAL(38,10)) = CAST(TRY_CAST(v AS BIGINT) AS DECIMAL(38,10))` |
| fixed-point (`DECIMAL(p,s)`) | `TRY_CAST(v AS DECIMAL(38,10)) = CAST(TRY_CAST(v AS DECIMAL(p,s)) AS DECIMAL(38,10))` |
| `DATE` | `TRY_CAST(v AS TIMESTAMP)` is null, or equals `CAST(TRY_CAST(v AS DATE) AS TIMESTAMP)` |
| `TIMESTAMP` (naive) | `TRY_CAST(v AS TIMESTAMPTZ) = CAST(TRY_CAST(v AS TIMESTAMP) AS TIMESTAMPTZ)` |
| `T[]` | `T`'s own predicate applied element-wise, via `list_transform` — never `TRY_CAST` on the list |
| `VARCHAR`, `BOOLEAN`, `UUID`, `TIMESTAMPTZ` | `TRY_CAST` alone — no lossy path exists from text |
| `DOUBLE` | `TRY_CAST` alone — see below |

`DOUBLE` is the one deliberate exception. Binary floating point cannot
represent most decimal fractions exactly, so an exact-carrier predicate
would reject `'0.1'`, and a producer who declares `DOUBLE` has asked for
approximate representation by choosing the type. `DECIMAL(p,s)` is the
type for a value whose exactness matters, and its predicate enforces that.

**The carrier is `DECIMAL(38,10)`, not `DOUBLE`, and this matters.** An
earlier draft used `DOUBLE`, which is not exact past ~15 significant
digits, so the carrier destroyed the very difference it was there to
detect: `'9007199254740992.4'` declared `BIGINT` casts to
`9007199254740992` and the `DOUBLE` comparison **accepted** it (verified).
With the `DECIMAL(38,10)` carrier the same value is rejected, as are
`'9007199254740993.7'` and `'1.5'`, while `'001'`, `' 5 '`, and `'1.0'`
still pass. A value too large or too precise for `DECIMAL(38,10)` makes
`TRY_CAST` return `NULL`, the comparison yields `NULL`, and the column
degrades — conservative in exactly the right direction.

**Arrays are element-wise, not whole-list.** DuckDB casts list children
individually, so `TRY_CAST(['1.5','2'] AS BIGINT[])` succeeds and yields
`[2, 2]` (verified) — the same rounding, one level down, and an earlier
draft's "arrays need `TRY_CAST` alone" would have accepted it. The
predicate for `T[]` is `T`'s predicate mapped over the elements:

```sql
list_reduce(
  list_transform(v, x -> CAST(<predicate for T on x> AS INT)),
  (a, b) -> a * b
) = 1
```

Four scalar predicates plus one recursion rule, each expressible as one
SQL comparison over the whole column. The rule **rejects loss and accepts
normalization**, which is the distinction that matters — verified
behaviour of the table above:

```text
'001'     -> BIGINT          accepted (1)
' 5 '     -> BIGINT          accepted (5)
'1.5'     -> BIGINT          rejected
'12.34'   -> DECIMAL(18,4)   accepted (12.3400 — scale padding is not loss)
'12.34567'-> DECIMAL(18,4)   rejected
'2024-05-10T00:00:00Z' -> DATE   accepted (midnight carries nothing)
'2024-05-10T14:30:00Z' -> DATE   rejected
```

A lexical round-trip (`CAST(CAST(v AS T) AS VARCHAR) = v`) was considered
and rejected as the rule: it fails `'12.34'` → `DECIMAL(18,4)` and every
ISO-8601 instant written with `Z` (DuckDB renders `+00`), degrading the two
most common well-formed cases in a bundle.

**Constraints are out of v1 entirely.** An earlier draft had `NOT NULL`,
`CHECK`, `UNIQUE`, `PRIMARY KEY`, and `DEFAULT` read and diagnosed but not
enforced. That is a second system — it needs `duckdb_constraints()`,
expression preservation, and a per-constraint evaluation semantics over
documents — and `DEFAULT` is not even a condition existing data can
violate. v1 reads exactly three things from a declaration: **column names,
column types, and comments** — `NOT NULL` included in what is not read, so
there is no "declared non-null" state to diagnose. A declaration carrying
constraints is still well-formed; the constraints are simply not read, not
enforced, and not diagnosed. They are the natural first extension once the
typing half is in use.

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

**This is a different outcome from a failing cast, and the two must not be
merged.** A failing cast (decision 5) means a *supported* type the data
does not currently satisfy: the column materializes as `VARCHAR` but the
declared type is still exported, because it is still an intelligible
statement of intent (decision 9). An unsupported type is not intelligible
to the exporter at all — there is no JSON Schema or Zod form for `STRUCT`
under decision 10's closed mapping — so exporting "the declared type"
would mean inventing one. An earlier draft routed both through a single
fallback and so said both things at once; they are now separate cases in
decision 7.

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
unreadable file, a shape decision 2/4 rejects (a `CREATE_TABLE_AS` plan
included), a DuckDB syntax error, a table name mismatch (decision 3), and
a derived-path collision (decision 1, both types). Failing-cast fallback
takes decision 5's predicate returning false for any row. Unsupported-type
fallback takes decision 5a's set, and a declared reserved column (decision
5b) behaves the same way. Undeclared observed fields and undeclared
catalog comments are diagnostics without a fallback — nothing degrades,
because nothing about them was declared.

Every case resolves to "ignore the unusable part, compile the rest exactly
as declared," never to a hard failure and never to discarding more than
the one thing that broke.

### 7a. `apply`: typed columns are readable, not writable, in v1

`apply` compiles declarations like every other surface — a declared
`TIMESTAMPTZ` column is a real `TIMESTAMPTZ` in the ephemeral table, so
`WHERE registrado_em > NOW() - INTERVAL 30 DAY` finally means what it says.
That is where most of the value is, and it is available immediately.

**The rule is "a typed column's stored value may not change," and it has
to be a value-level check — a schema-level one does not catch the failure
mode.** An earlier draft proposed reusing `_check_result_schema`
(`apply.py:690-724`), which only compares each column's *type* before and
after; `UPDATE "Rotina" SET custo = custo * 1.10` leaves `custo`
`DECIMAL(18,4)` on both sides of that comparison; it passes, and would
reach `apply`'s frontmatter compile step, whose rule is
`target = final_value if isinstance(final_value, str) else None`
(`apply.py:764-798`) — every `Decimal` compiles to `None`, and a `None`
final value for a field that was present compiles to deletion. The
schema-level check does not see this at all; it is one column with one
type throughout.

So the guard is a new one, run per typed column before
`_compile_row_diff`: for every row, the column's value in `before` and in
`after` must be equal (`IS NOT DISTINCT FROM`, so `NULL` compares as
`NULL`). Any row where they differ is `ApplyError`, dry-run or not, naming
the column and the type declaration that made it read-only — a single SQL
comparison per typed column, not a value-by-value walk in Python.

**`RENAME COLUMN` on a typed column needs the same guard, not an
exemption.** `ALTER TABLE "Rotina" RENAME COLUMN custo TO valor` changes
`custo`'s presence in `before`'s column set entirely, so the equality
check above has nothing to compare it against — and `_compile_field_value`
would still read the renamed column's final value, find it is not a
`str`, and compile it to a deletion under the new name, silently losing
the value on every row. `RENAME COLUMN` naming a typed column is therefore
rejected outright in v1, with a message pointing at the same declaration.
A caller who wants the rename edits the declaration file and, until RFC
0007 gives that edit a writeback path, drops back to `VARCHAR` for that
column deliberately (by removing it from the declaration) before renaming
it through `apply`.

Both rules reduce to one sentence: **a declaration makes a column
`apply`-readable; it does not make it `apply`-writable, in any form**, not
"disallowed only when it appears in `SET`." Making that work — a canonical
YAML serialization per type, decided value by value: whether `12.34`
becomes `12.3400` or `12.34`, how a typed `NULL` relates to the
absence/`null` distinction RFC 0005 maintains, what a quoted-by-the-author
value does — is the same problem RFC 0007 already owns for declaration
writeback, so it goes there, whole, rather than being solved once here and
again there.

### 7b. `duckdb`: declared types materialize in a `{schema}_types` schema

`duckdb`'s persistent output is four tables — `concepts`, `links`,
`reserved`, `diagnostics` — and per-type tables are not among them. This
RFC does not reshape that contract: declared types materialize as one
table per type in a **second schema**, named `{schema}_types` for the
`--schema` the command was given (`okf` → `okf_types`), each table named
for the exact authored type value, quoted, as RFC 0005 names its ephemeral
tables.

A separate schema, rather than the existing one, because a type named
`concepts` would otherwise collide with the contract's own table, and
because it makes the whole typed surface droppable and inspectable as a
unit.

**`{schema}_types` is exclusively owned, and ownership is asserted rather
than assumed — a schema-wide "drop what I don't recognize" over a name a
caller chose is exactly how a differently-owned table gets destroyed.**
`duckdb` refuses to run against a `{schema}_types` that already exists and
contains any table this command did not itself create in a prior run:
concretely, a schema comment DuckDB's `COMMENT ON SCHEMA` attaches on
first creation — `okf-parser managed: do not create objects in this
schema` — that a subsequent run checks for before touching anything.
Absent on the schema (first run, or a name reused from something else): if
the schema is empty, it is created and stamped; if it already holds any
table, `duckdb` refuses to write to it at all and reports which tables it
found, rather than guessing whether they are safe to drop. Present and
matching: the run proceeds, and only tables it manages — every table whose
name is a currently-declared type — are subject to the create-or-replace,
drop-if-type-gone cycle decision 7b already describes. A table under a
stamped schema that the command didn't create in this or a prior run
cannot arise, because the stamp is the one gate for entering the schema at
all.

This is deliberately the strictest of the three options considered
(refuse-to-adopt, rather than never-delete or a manifest table): a
manifest is one more piece of state that can drift from the schema it
describes, and never-deleting means a renamed or removed type's table
accumulates forever. Refusing adoption of anything unrecognized costs
nothing on the common path — a schema this command created is never a
foreign object — and fails loudly, at the one moment (first run against an
unexpected `{schema}_types`) where a wrong guess would otherwise be
silent.

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

Every place a type is named downstream — DDL this RFC issues, diagnostics,
the `schema` export — uses the spelling DuckDB's catalog reports
(`DECIMAL(18,4)` regardless of the whitespace or case the author wrote),
never the file's original text a second time. The declaration file itself
is never rewritten by this RFC, so the author's formatting is preserved
where it lives.

The export mapping is stated exhaustively, because decision 5a's closed
type set exists precisely so that it can be:

| DuckDB | JSON Schema | Zod |
| --- | --- | --- |
| `VARCHAR` | `string` | `z.string()` |
| `BIGINT` | `integer` | `z.number().int()` |
| `DOUBLE` | `number` | `z.number()` |
| `DECIMAL(p,s)` | `string`, `pattern` for the scale | `z.string()` |
| `BOOLEAN` | `boolean` | `z.boolean()` |
| `DATE` | `string` + `format: date` | `z.string()` |
| `TIMESTAMP` | `string` + `format: date-time` | `z.string()` |
| `TIMESTAMPTZ` | `string` + `format: date-time` | `z.string()` |
| `UUID` | `string` + `format: uuid` | `z.string().uuid()` |
| `T[]` | `array` of `T`'s row above | `z.array(...)` |

`DECIMAL` maps to `string`, not `number`: JSON's number is a double, so
exporting a fixed-point column as `number` would hand a consumer the exact
precision loss decision 5 refuses to accept on the way in.

For everything else, the rest of the mapping follows the JSON Schema
vocabulary `schema_export.py` already emits — the exporter's
existing vocabulary, not a new one.

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

## Open questions

- Exact `OKF0xx` codes for decision 7's cases — the existing numbering runs
  through `OKF010`, and the assignment should be made against
  `bundle.py`/`type_specs.py` rather than fixed here in isolation.
- Whether `{schema}_types` (decision 7b) should hold tables or views. This
  RFC says tables, matching the four contract tables; views would avoid the
  copy but need the source relation to be queryable from the persisted
  database, which the current output does not guarantee.
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
