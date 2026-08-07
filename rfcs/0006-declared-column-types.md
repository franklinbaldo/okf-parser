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

Declaration buys **typed intent without sacrificing source fidelity**. A
declared field stays typed even when individual rows diverge: DuckDB's
`TRY_CAST` is applied per value, and an incompatible value becomes `NULL`
only in the typed projection while the raw carrier remains available. One
bad document never widens the whole column back to `VARCHAR`. Divergence is
advisory by default (decisions 5 and 8).

v1 reads column names, normalized logical types, and comments from the
declaration catalog. Constraints are not read. `schema` exports the lossless
type IR; persistent `duckdb` materializes raw plus typed snapshot columns;
`apply` is the remaining integration step and will use the same projection as
a generated read-only column (decision 7a). Nothing writes back into the
declaration file (deferred to RFC 0007).

**A declared column is never the only copy of a document's data.** The
compiler keeps a raw carrier beside the typed projection — `VARCHAR` for a
scalar field and `VARCHAR[]` for declared `T[]`. `duckdb` stores the typed
projection as a snapshot value because DuckDB generated columns are currently
virtual; `apply` uses a generated column because there the database-enforced
read-only property is useful. Both consumers use the same DuckDB `TRY_CAST`
semantics. The raw column is compiler-owned under the existing `__okf_`
boundary.

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

### 5. Raw is the truth; typed is a per-value DuckDB projection over it

This decision is closed. A declared field has two logical representations: a
compiler-owned raw carrier and a public typed projection. The raw column is
`VARCHAR` for a scalar and `VARCHAR[]` for `T[]`; the typed value is always
DuckDB's own `TRY_CAST` of that carrier.

The two relational consumers choose different physical storage for the same
projection. Persistent `duckdb` stores the typed value as an ordinary column,
computed once during export with `TimeZone = 'UTC'`, so reopening the database
under a different session timezone cannot change the snapshot. `apply` uses a
DuckDB generated column over the same raw carrier, because DuckDB then enforces
that the typed projection is read-only. Generated columns are virtual in the
DuckDB version targeted here, so they are deliberately not used as persistent
snapshot storage.

**There is no whole-column fallback anymore.** One divergent document does not
turn every other row back into `VARCHAR`. `TRY_CAST` is evaluated per value: a
value DuckDB cannot cast becomes `NULL` in the typed projection while the authored
text remains unchanged in `__okf_raw_<field>`. For `T[]`, DuckDB's list cast owns
the same rule element by element; the raw list remains available for audit.

**DuckDB's cast semantics are normative.** This RFC no longer defines lexical
regular expressions for integers, decimals, dates, or timestamps and no longer
tries to prove that a cast is "exact" by round-tripping through a carrier type.
If DuckDB accepts a cast that normalizes, rounds, or truncates a representation,
that normalized value is precisely what the typed projection means; the raw value
beside it is the lossless record. A diagnostic may compare raw and typed values for
observability, but that comparison never decides whether the typed column exists.

This split is what makes the rule safe: a physical type is allowed to be physical
without being asked to preserve author spelling. `apply` protects every generated
field and its `__okf_raw_` source under the existing compiler-owned `__okf_`
boundary; user SQL does not write the generated column directly.

Constraints remain out of v1. `NOT NULL`, `CHECK`, `UNIQUE`, `PRIMARY KEY`, and
`DEFAULT` may exist in the declaration script but are not part of the contract read
out of the catalog. v1 reads column names, normalized logical types, and comments.

### 5a. The v1 type IR preserves DuckDB identity before target-specific export

DuckDB parses every authored type. The compiler then stores the normalized catalog
spelling in a lossless logical-type IR rather than immediately flattening it to the
old six-value `CastKind` vocabulary. The first supported families are:

- `VARCHAR`, `BOOLEAN`;
- signed and unsigned integer families through `HUGEINT`/`UHUGEINT`;
- `FLOAT`/`DOUBLE`;
- `DECIMAL(p,s)`, preserving precision and scale;
- `DATE`;
- naive timestamp variants (`TIMESTAMP`, `TIMESTAMP_S`, `TIMESTAMP_MS`,
  `TIMESTAMP_NS`);
- `TIMESTAMPTZ` / `TIMESTAMP WITH TIME ZONE`;
- `UUID`;
- list (`T[]`) composition over the supported scalar families.

An unknown or future DuckDB type is not erased. Its normalized `data_type` survives
in the IR as `unsupported`, so JSON Schema can still carry
`x-okf-duckdb-type` and diagnostics can name the real physical declaration. A target
that cannot faithfully represent that family emits no invented standard type.

`CastKind` remains the vocabulary for legacy observation inference and explicit
`--cast`; it is no longer the representation of a `.schema.sql` declaration.

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
- **data divergence has no schema fallback**. When the declaration is
  well-formed but one value does not cast (decision 5), only that row's typed
  projection is `NULL`; its raw carrier remains available, the column stays
  declared, and the comment still applies;
- **target capability is local to the target**. A DuckDB physical type that a
  JSON/Zod exporter cannot standardize remains representable by the DuckDB
  materializer using its catalog spelling; narrower exporters keep the exact
  type in metadata rather than inventing a false standard type (decisions 5a
  and 10).

Enumerated, the cases that reach each: whole-declaration fallback takes an
unreadable file, a script that fails to execute, a post-condition decision
2 rejects (zero, two, or a differently-named table left behind — decision
3's identity check), and a derived-path collision (decision 1, both
types). Failing-cast fallback
no longer has a failing-cast branch: row-level `TRY_CAST` owns data
divergence. Target-specific representation limits are handled by each exporter,
not by degrading the DuckDB materialization. A declared reserved column
(decision 5b) remains outside the public typed field set. Undeclared observed
fields and undeclared catalog comments do not degrade any declaration.

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

**The persistent typed column is a stored snapshot, not a virtual generated
column.** DuckDB 1.5 supports only virtual generated columns, which are
recomputed when read. That is ideal for `apply`'s ephemeral read-only
projection but wrong for an exported database: in particular, converting an
offset-less string to `TIMESTAMPTZ` depends on the session `TimeZone`. The
persistent materializer therefore fills ordinary typed columns with the same
per-value `TRY_CAST` under a temporary `TimeZone = 'UTC'` and restores the
caller's setting immediately afterward. The exported typed value is stable
across later sessions; the raw carrier remains beside it for audit.

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

### 9. `schema` exports declared intent; observations decide presence, not type

This decision is closed. When `.schema.sql` declares a field, `schema` exports that
declared logical type even if one or more current documents do not cast to it. A
malformed row is data drift, not a request to silently widen the producer's
contract. This matches decision 5's per-value materialization: the future typed
column remains typed and only that row's `TRY_CAST` result becomes `NULL`.

`--infer-types` is therefore ignored for a declared field. An explicit operator
`--cast FIELD=TYPE` remains the one intentional override and wins over the
declaration for that invocation; unlike a declaration it is strict against the
observed values, preserving the command's existing contract.

Presence and nullability remain observational. A declared-but-unobserved column is
exported as optional; a field present in every document is required; authored YAML
`null` controls nullability independently. Constraints are not read from DDL, so the
declaration supplies no competing presence rule.

Declared/effective/observed multi-mode output can still be added later if callers
need all three views at once, but it is no longer required to answer what today's
`schema --spec-template` means: it is the producer's declared type plus the bundle's
observed presence/nullability.

Divergence diagnostics remain advisory and do not alter the emitted schema. The
diagnostics transport described by decision 8 can be implemented independently of
this contract decision; until then, declaration parsing failures are surfaced
explicitly and value drift does not change output shape.

### 10. Physical types stay lossless in the IR and map per export target

This decision is closed. DuckDB's catalog spelling is the physical identity carried
through the shared contract. Every declared JSON Schema property also exposes it as
`x-okf-duckdb-type`, so precision, timestamp semantics, unsigned width, and future
types are never lost merely because a target has a smaller vocabulary.

The target mappings are intentionally not identical:

| DuckDB family | JSON Schema | Zod |
| --- | --- | --- |
| `VARCHAR` | `string` | `z.string()` |
| safe-width integer | `integer` | `z.number().int()` |
| `BIGINT`/wider integer | `integer` | `z.union([z.number().int(), z.bigint()])` |
| `FLOAT`/`DOUBLE` | `number` | `z.number()` |
| `DECIMAL(p,s)` | `number` + `multipleOf(10^-s)` | `z.string()` |
| `BOOLEAN` | `boolean` | `z.boolean()` |
| `DATE` | `string`, `format: date` | `z.iso.date()` |
| naive `TIMESTAMP*` | `string` + `x-okf-temporal-kind` | `z.string()` |
| `TIMESTAMPTZ` | `string`, `format: date-time` | `z.iso.datetime({ offset: true })` |
| `UUID` | `string`, `format: uuid` | `z.uuid()` |
| `T[]` | array of `T` | `z.array(T)` |

JSON Schema numbers are mathematical JSON-number instances, not an IEEE-754 storage
promise, so fixed-point `DECIMAL` remains numeric there and retains its DuckDB type
annotation. Zod runs in JavaScript, where exact decimals have no native primitive;
its declared DECIMAL representation is therefore a string until a decimal codec or
library is explicitly selected. Likewise, wide integers accept native `bigint`
instead of pretending every DuckDB `BIGINT` is a safe JavaScript `number`.

A naive DuckDB `TIMESTAMP` is not RFC 3339 `date-time`: it carries no offset, so the
JSON Schema exporter does not misuse that standard format. `TIMESTAMPTZ`, whose
string representation carries an offset, maps to `date-time` normally.

Pydantic follows Python's richer runtime vocabulary: declared `DECIMAL` maps to
`Decimal`, `UUID` to `UUID`, both timestamp families to `datetime`, and integer
families to arbitrary-precision `int`.

An unsupported DuckDB family keeps only `x-okf-duckdb-type` in JSON Schema and maps
to an unconstrained target rather than being mislabeled as `string`.

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
