---
type: RFC
title: Relational writes to frontmatter fields
status: proposed
description: Define an `apply` command that mutates frontmatter fields by handing a bounded ALTER TABLE + UPDATE script to an in-memory DuckDB database of exact-type tables, with round-trip fidelity, dry-run, and stage-then-validate-then-write semantics
---

# RFC 0005: Relational writes to frontmatter fields

## Summary

Add `okf-parser apply PATH --sql 'UPDATE "Rotina" SET ... WHERE ...'`, a
command that mutates frontmatter fields in place across a bundle. `apply`
materializes every concept type into its own table, named for the exact
authored `type` value, quoted, inside one in-memory DuckDB database — one
table per type, mirroring how `schema_contract.py` already compiles one
`TypeContract` per type rather than reconciling every type's fields into one
shape. The caller's `--sql` is handed to that database mostly as-is: DuckDB
itself parses it, resolves the target identifier (Unicode, quoting, case
folding, all of it), and executes the statements. `apply` does not
reimplement any part of a SQL parser or identifier resolver — it validates
the script's *shape* (zero or more leading `ALTER TABLE ADD/DROP/RENAME COLUMN` statements, one trailing `UPDATE`, via DuckDB's own
`extract_statements()`) and derives everything else, including which type
was touched, from a before/after diff of the tables it built.

`apply` reuses the existing relational compilation (`duckdb`, `schema`) to
select and express the mutation, but writes back through a purpose-built
round-trip YAML loader instead of re-serializing frontmatter wholesale — a
new capability, not one borrowed from `format`, which never touches
frontmatter content at all (`mdformat-frontmatter` treats it as an opaque,
passed-through block). `apply` does adopt `format`'s "refuse rather than risk
a bad rewrite" posture — skip and report a document instead of writing
something lossy — and its dry-run-by-default convention.

Dry-run is the default, matching `format`: `apply` without `--write` reports
which documents and fields would change; nothing is written until `--write`
is explicit.

## Motivation

The bundle already compiles one direction: filesystem to relations
(`inventory`, `graph`, `duckdb`). Nothing compiles the other direction. The
only command that touches a file is `format --write`, and it rewrites
Markdown style, never a frontmatter value.

Reported in #26, from a day of concrete use rather than a hypothetical:

1. **A type's schema changed three times in one day.** Fields entered and
   left frontmatter between runs of the same routine. Editing by hand works
   at three concepts; the same edit at thousands is not viable.
2. **An architecture decision was made by the absence of this feature.**
   Making `type` hold the path to its own specification document was
   evaluated and partly rejected because migrating 45,705 existing concepts
   by hand was impractical. With relational writes, that migration cost stops
   being the deciding factor.
3. **Backfilling a field.** Older concepts lack `timestamp`; populating it
   from another source is a column assignment.
4. **Normalizing values.** Inconsistent timezone offsets, a field spelled
   differently across documents, a slug outside the house style.

## Why the concept relation cannot be `UPDATE`d directly — and why one table isn't the fix either

`bundle.concepts` (`bundle.py`) has one column per *promoted* field
(`concept_type`, `title`, `description`) and a single `frontmatter_json`
column — but `frontmatter_json` already serializes `self.frontmatter`
wholesale (`models.py`), so it *already contains* `type`, `title` and
`description`. Flattening it naively and keeping the promoted columns
alongside produces two names for the same value (`title` twice,
`concept_type` next to `type`) and no name at all for `setor`.

An earlier draft of this RFC fixed that by flattening every observed
frontmatter key, across every type in the bundle, into one wide table. That
traded one problem for another: two types can use the same field name for
unrelated things, so the table's own type inference (reusing
`schema_contract.py`'s per-type discovery, but applied across the whole
bundle) would have to reconcile shapes that were never meant to agree, and a
`WHERE` clause that meant to scope one type could silently match rows of
another that happens to share a field name.

**One table per type** removes the reconciliation instead of managing it —
each table only ever holds one type's fields, mirroring `schema_contract.py`'s
one-`TypeContract`-per-type discovery. Two earlier drafts got the
*identifier* wrong before landing here:

- **Named the table by the type's slug** (the same function `type_specs.py`
  derives type-spec document paths with), so `UPDATE rotina SET ...` would
  select "the `Rotina` type" by naming its table. Broken:
  **`type_slug()` is not injective.** This RFC's own motivating example is
  the counterexample — `"Revisao Ciencia"` and `"Revisão Ciência"` both slug
  to `revisao-ciencia`, because the slug strips accents by design
  (`changelog/0.14.0.md`). A table name derived from a lossy function cannot
  reliably select exactly one type.
- **Decoupled type identity into a required `--type` flag**, always
  exposing the result to `--sql` under one fixed name, `concepts`. This
  worked, but review pointed out it was solving a self-inflicted problem:
  DuckDB supports **quoted Unicode identifiers**, so there was never a
  reason to slug — or to introduce a second flag alongside the SQL — in the
  first place. `type` is already the bundle's canonical producer-defined
  identity; a table name that isn't the exact authored value is an
  unnecessary derived layer sitting next to it.

So the table name **is** the exact authored `type` value, always as a
quoted identifier: `UPDATE "Revisão Ciência" SET ...`. No flag, no slug, no
normalization, and — per a further round of review — no custom parsing
either: `apply` builds one table per distinct `type` value observed in the
bundle inside a single in-memory DuckDB connection, then hands the caller's
`--sql` straight to that connection. DuckDB's own binder resolves whatever
identifier the statement names, Unicode and all; `apply` never re-derives
"which type does this string mean" itself.

Within each materialized table, the writable namespace is that type's
authored frontmatter keys, each exactly once, plus structural columns the
parser itself owns, under a reserved `__okf_*` prefix — filesystem-derived
identity (`__okf_path`, `__okf_concept_id`, `__okf_logical_key`) and, per
1d below, the document body — none of which can collide with an authored
key. `apply` refuses to run against any type where an observed key already
starts with `__okf_`, rather than silently shadowing it. The prefix's scope
is deliberately broader than "filesystem identity": it means *any* column
`apply` itself puts in the table, as opposed to one unnested from authored
frontmatter.

DuckDB identifiers are **case-insensitive even when quoted** — a genuine
DuckDB quirk, not standard SQL, confirmed against its own test suite earlier
in review. For columns, that still needs an explicit check: two distinct
YAML keys that only differ by case (`setor` / `Setor`) cannot become two
separate writable columns, and `apply` refuses to compile a table where two
observed keys collide under it, naming both. For **type** identity, though,
materializing every type up front means the collision surfaces for free:
creating `CREATE TABLE "Rotina" ...` and then `CREATE TABLE "ROTINA" ...` in
the same DuckDB connection is DuckDB's own "table already exists" error,
because the two fold to one identifier. `apply` catches that specific error
during setup and reports it as a named collision between the two
producer-defined types, rather than either silently overwriting one table or
surfacing DuckDB's generic message unexplained.

A concept with no `type` at all, or a reserved document (`index.md`,
`log.md`; see `RESERVED_FILENAMES` in `parser.py`), matches no `type`
identifier and `apply` cannot target it; `check`/`inventory` remain where an
untyped concept is visible.

## Decision

### 1. Materialize every type as a table; let DuckDB run the whole script

DuckDB's `UPDATE` only targets tables, not views — `UPDATE v1 ...` against a
view is a binder error. `apply` builds one **temporary table per distinct
`type` value** observed in the bundle, all inside a single in-memory DuckDB
connection, each named for its type as an exact quoted identifier
(`CREATE TEMP TABLE "Rotina" AS ...`), unnesting `frontmatter_json` into one
column per authored key observed on concepts of that type using
`schema_contract.py`'s existing per-type discovery, plus the reserved
`__okf_*` columns (identity per decision 4, body per 1d). A second, untouched
copy of every table — an internal `_before` set, never exposed to the
caller's SQL — is kept alongside for step 2's diff.

`apply` does not parse identifiers out of `--sql` at all, and does not
decide up front which type is being mutated. The caller's SQL is handed to
the same connection essentially as written; DuckDB's own parser and binder
resolve whatever table the statement names, Unicode and case-folding
included. Which type actually changed is discovered **after** execution, by
diffing every table against its `_before` copy (decision 2) — not declared
beforehand by `apply` reading the SQL text.

```bash
okf-parser apply ./bundle --sql "
  UPDATE \"Rotina\"
     SET setor = '#GAB#FSB'
   WHERE setor IS NULL AND __okf_path LIKE 'rotinas/%'
" --write
```

A simpler surface without `WHERE` is also offered for the common case, so a
caller who only needs an unconditional field change does not need to write
SQL — though see 1b for why a rename of `type` itself is not yet a settled
part of this design:

```bash
okf-parser apply ./bundle --type "Rotina" --field setor --from "GAB" --to "#GAB#FSB"
```

`--field/--from/--to` compiles to exactly the SQL form would
(`UPDATE "TYPE" SET field = to WHERE field = from`) and is executed through
the identical path — no host-side special case, so it has no capability the
SQL form lacks (in particular, see 1c: it cannot introduce a field that
does not already exist as a column any more than raw SQL can). `--type` is
required only here, because the sugar has no `UPDATE` statement to read the
target identifier out of.

### 1a. `--sql` is a bounded migration script: leading `ALTER TABLE`, one trailing `UPDATE`

An `UPDATE` alone cannot introduce a field no selected document has yet —
that is not SQL semantics, and 1c's earlier draft (`apply` synthesizing a
column by parsing the `SET` list) was exactly the kind of custom SQL
handling this design otherwise avoids. The SQL-correct way to add a field is
`ALTER TABLE ... ADD COLUMN`, executed in the same connection before the
`UPDATE`. So `--sql` accepts a short, structurally bounded script rather
than a single statement: zero or more leading statements, each classified
by DuckDB's own statement-type introspection (`extract_statements()`) as an
`ALTER TABLE` using only `ADD COLUMN`, `DROP COLUMN`, or `RENAME COLUMN`,
followed by exactly one trailing statement classified as `UPDATE`. Anything
else — a second `UPDATE`, `DELETE`/`INSERT`, any DDL besides those three
`ALTER TABLE` forms, a script with no trailing `UPDATE` — is rejected before
anything runs.

The whole script executes as one DuckDB transaction. `ALTER TABLE` is
transactional in DuckDB, so a script that fails partway (a later statement
errors, an added column's type is later rejected by 1c's scalar-only rule)
rolls back cleanly inside the in-memory connection; nothing about the real
bundle is ever at risk, since nothing has touched a real file yet at this
point regardless.

Bounding the script's *shape* is necessary but not sufficient — the earlier
draft also bounded it to naming exactly one table, by parsing the target
identifier. This design gets the same bound without parsing: after the
script runs, `apply` diffs **every** table (schema and rows) against its
`_before` copy. If more than one table differs at all, `apply` refuses to
write anything — a script that reaches across types was never a supported
shape, and this is where that gets caught, structurally, rather than by
inspecting identifiers before execution.

### 1b. Undecided: what happens when the mutation changes `type` itself

Motivation case 2 above — renaming `"Revisao Ciencia"` to `"Revisão Ciência"`
in bulk — is a `type` rewrite. Since the table is named for the *pre*-mutation
type, `type` inside it is, mechanically, an ordinary column like any other —
the open question is whether `apply` lets `SET` touch it at all. This RFC
does not yet pick between two ways to handle it, and is not marked
`accepted` until it does:

- **Let it migrate.**

  ```bash
  okf-parser apply ./bundle --sql "
    UPDATE \"Revisao Ciencia\" SET type = 'Revisão Ciência'
  " --write
  ```

  The table name already selected every row with the exact old value, so
  the `UPDATE` needs no `WHERE` at all for the pure-rename case. At write
  time (decision 5), `apply` determines each document's destination purely
  by its post-mutation `type` value, independent of the table it was staged
  under for this invocation — this falls out of diffing by
  `__okf_concept_id`, not a new mechanism. `check` (already run against the
  full candidate bundle in decision 5) is what would catch a destination
  type without a matching spec, if `--require-spec` is in play.

  This is where an earlier draft's idempotency argument stops holding, and
  the honest statement replaces it rather than being quietly dropped:
  because decision 1 now materializes a table only for types **currently**
  observed in the bundle, running the invocation above a second time — after
  every `"Revisao Ciencia"` concept has already migrated — finds no table
  named `"Revisao Ciencia"` at all, and the `UPDATE` fails with DuckDB's own
  "table does not exist" error, not a graceful zero-row no-op. `apply` does
  not promise that literally rerunning a converged `--sql` invocation
  succeeds; decision 6 states precisely what it does promise instead.

- **Forbid it.** `type` becomes read-only inside `apply`, like `__okf_path`.
  Bulk type rename gets a dedicated command
  (`okf-parser rename-type FROM TO`, out of this RFC's scope) instead of
  being one case of a general `UPDATE`.

The rest of this RFC is written assuming `type` behaves like any other
column unless a table migration is explicitly not possible for some other
reason surfaced during review; §6 (idempotency) and the write path in
decision 5 do not change under either choice, only what `apply` accepts.

### 1c. v1 scope: top-level scalars only, explicit schema changes, explicit null contract

"One column per authored key observed anywhere on concepts of that type"
(decision 1) leaves several things undefined that must not stay implicit:

- **Introducing a field.** Backfilling (motivation case 3) needs a field no
  selected document has yet. That is not something a plain `UPDATE` can do —
  `SET timestamp = ...` fails to bind against a column that does not exist,
  which is ordinary SQL, not a defect. 1a's leading `ALTER TABLE ... ADD COLUMN "timestamp" VARCHAR` is the supported way to introduce a field, run
  once ahead of the `UPDATE` that populates it. `apply` does not read the
  `SET` list and synthesize a column behind the caller's back — an earlier
  draft did exactly that, and review correctly called it custom SQL handling
  the design should not need. `ADD COLUMN` must specify `VARCHAR`; any other
  type is rejected — see the scalar-only rule below.
- **Removing a field bundle-wide versus per-row.** `ALTER TABLE ... DROP COLUMN` removes a field from every concept of a type at once, regardless
  of `WHERE` — the SQL-level, bulk operation. `SET field = NULL` remains the
  row-level operation, deleting the key only from rows the `WHERE` clause
  matches. The two are not redundant: a bundle-wide field removal (a
  deprecated key gone from the type's own spec) and a per-row backfill gap
  (an authored `null` versus a genuinely absent key on specific documents)
  are different operations with different blast radii, and this design gives
  each an explicit one instead of overloading `SET ... = NULL` to mean both.
- **Renaming a field bundle-wide.** `ALTER TABLE ... RENAME COLUMN` is now
  available for the same reason `ADD`/`DROP COLUMN` are — motivation case 1
  ("a field spelled differently across documents") is a rename, not a value
  rewrite, and SQL already has the right verb for it.
- **Absent key versus authored `field: null`.** Both collapse to SQL `NULL`
  in a flattened column, which is fine for reading but ambiguous for
  writing: does `SET field = NULL` delete the key, or author a literal YAML
  `null`? v1 picks one behavior instead of leaving both live: **`SET field = NULL` deletes the key.** Authoring an actual YAML `null` value through
  `apply` is out of v1's scope — a real limitation, stated here rather than
  left for an implementer to discover.
- **List/map-valued fields.** v1's writable namespace is **top-level scalar
  fields only** (string, number, bool, date — the same scalar kinds
  `schema_contract.py` already infers), each represented as `VARCHAR` with
  lexical semantics identical to how `parser.py` already preserves scalar
  spelling on read. A field whose observed value is a YAML list or mapping on
  any selected document is excluded from the table entirely, and any `ADD COLUMN` naming a non-`VARCHAR` type is rejected once the script's result is
  inspected (decision 4) — not by parsing the script beforehand, consistent
  with 1a's "run it, then validate the outcome" posture. Structured writes
  are real future work, not silently unsupported.

### 1d. Read-only body columns for content-aware queries

A concept is frontmatter *and* Markdown body — `models.py`'s
`ParsedDocument` and `ConceptRecord` already model it that way, and
`bundle.concepts` already carries a `body` column (`bundle.py`,
`_CONCEPT_SCHEMA`). Exposing it in `apply`'s materialized tables adds no new
entity to the domain; it makes something already modeled queryable
alongside frontmatter instead of leaving it an opaque blob a caller can only
read one document at a time.

Every table gets two additional reserved columns, read-only, alongside the
`__okf_*` identity columns:

- `__okf_body VARCHAR` — the exact Markdown body, byte-for-byte, as
  `ParsedDocument.body` already holds it;

- `__okf_body_lines VARCHAR[]` — a derived, line-split projection of
  `__okf_body`, one array element per line, for `unnest`-based queries:

  ```sql
  SELECT __okf_path, generate_subscripts(__okf_body_lines, 1) AS line_number,
         unnest(__okf_body_lines) AS line
    FROM "Rotina"
   WHERE list_contains(list_transform(__okf_body_lines, l -> contains(l, 'TODO')), true);
  ```

Not `body`, unqualified: `bundle.concepts` already uses that name for its
own promoted column, and nothing stops a producer from also having a
frontmatter key literally called `body` — the same collision class decision
1's "why not `concept_type` next to `type`" already ruled out once. The
`__okf_` prefix sidesteps it the same way it does for identity.

`__okf_body` is the source; `__okf_body_lines` is a convenience index over
it, not a second representation. The split is deliberately lossy — it
cannot by itself distinguish a trailing newline's presence or `LF` from
`CRLF`, which is exactly why `__okf_body` stays canonical and no write path
is defined in terms of the line array.

**Both are read-only in this RFC.** Allowing `SET __okf_body_lines[3] = '...'` would turn this proposal from relational writes to frontmatter into
a transactional Markdown editor — body reconstruction, line-ending
policy, list/heading/fenced-block editing, and byte-for-byte preservation
of everything decision 3 already promises for frontmatter would all need
answers this RFC does not have. Decision 4's post-execution check extends
to both: any `ALTER TABLE` touching `__okf_body`/`__okf_body_lines`, or any
row where either value differs from its `_before` copy, discards the
result the same way tampering with `__okf_path` does. Writable body
content is real future work, explicitly out of this RFC's scope, not a gap
left open by omission.

### 2. Diff before write: every table, schema and rows, one document at a time

After the script runs, `apply` diffs **every** materialized table against
its `_before` copy — both its schema (which columns exist, and their types:
this is what 1a's "at most one table differs" rule and decision 4's
protected-column and scalar-type checks are computed from) and its rows,
keyed by `__okf_concept_id`. Only documents with at least one changed
*authored-column* value (or, if 1b resolves to "let it migrate," a changed
`type`) are candidates for writing; a changed `__okf_*` value is never a
write candidate — decision 4 treats that as a reject condition for the
whole result, not a document to stage. The diff, not the script text, is
what a `--write`-less run reports — this keeps the dry-run output
meaningful even when the mutation's `WHERE` clause matches nothing, and it
is also the only place `apply` learns which table (therefore which type)
was actually mutated, since nothing upstream of this step ever parsed that
out of the SQL.

### 3. Round-trip fidelity through a real YAML round-trip loader

`parser.py` currently loads frontmatter with a customized `yaml.SafeLoader`
that preserves scalar spelling but not comments, key order, or blank lines —
adequate for reading, useless for rewriting a human-authored file without
collateral damage. Writing requires swapping the load path for one that
preserves structure, most plausibly `ruamel.yaml`'s round-trip mode.

`apply --write` must, for every document it touches:

- preserve the Markdown body byte-for-byte;
- preserve every frontmatter key it did not change, including order;
- preserve YAML comments and blank lines inside frontmatter;
- preserve the file's original BOM and line-ending style.

This is a **new hard requirement for `apply`**, not one inherited from
`format`: `formatting.py`'s `format_path` today reads and writes with plain
`Path.read_text`/`write_text`, which normalizes line endings and drops a BOM,
and has no test asserting otherwise. `apply` needs its own preservation logic
and its own fixtures; it cannot borrow a contract `format` does not actually
have.

If the round-trip loader cannot parse a matched document's frontmatter
losslessly (an anchor, a merge key, a construct `ruamel.yaml` cannot
round-trip), `--write` aborts the **whole invocation** and writes nothing,
reporting every such document — not a per-document skip. `format` skips a
file and rewrites the rest because canonicalizing one document has no
bearing on any other; `apply --write` is usually a schema migration across
many documents at once, and silently writing the easy files while leaving
the hard ones behind produces a bundle in a mixed, half-migrated state that
is arguably worse than not running at all. Dry-run still reports every
blocker in one pass, so a caller sees the full list before deciding anything.
A future `--allow-partial` could opt into the weaker per-document-skip
behavior explicitly; it is not the default.

### 4. Refuse non-writable columns and non-scalar types — after execution, not before

`__okf_path`, `__okf_concept_id`, `__okf_logical_key`, `__okf_body`, and
`__okf_body_lines` are all derived or parser-owned, not authored; `SET __okf_path = ...` is a rename, `SET __okf_body = ...` is the transactional
Markdown editor 1d explicitly declines to be — neither is an implemented
operation. Consistent with 1a and 1c's posture, `apply` does not parse the
script to forbid this in advance — the whole script already ran, inside the
ephemeral in-memory connection, touching nothing real. Instead `apply`
inspects the outcome (decision 2's schema and row diff) and refuses to
write anything if any of the following holds:

- the set of `__okf_*` columns present differs at all from before (an
  `ALTER TABLE` added, dropped, or renamed one — including `__okf_body` or
  `__okf_body_lines`);
- any row's `__okf_path`, `__okf_concept_id`, `__okf_logical_key`,
  `__okf_body`, or `__okf_body_lines` *value* differs from before (a plain
  `UPDATE` targeted one, which the column still structurally allowing does
  not make legitimate);
- any `ADD COLUMN`/`RENAME COLUMN` in the resulting schema, other than the
  parser-owned `__okf_*` columns, is typed anything other than `VARCHAR`
  (1c's scalar-only rule);
- row identity or cardinality changed — a row's `__okf_concept_id` present
  before is missing after, or vice versa, which a bare `UPDATE` should never
  produce and is checked as a defensive invariant rather than assumed.

Any of these discards the whole in-memory result and reports why; nothing
about the real bundle is touched either way, since decision 5 has not begun
yet at this point.

### 5. Validate before writing: snapshot, stage, validate against baseline, then replace

Issue #26 requires aborting when a mutation *creates* nonconformance, not
reporting it after the fact and not requiring the input to already be clean
— a bundle `check` already flags (which is exactly the state a repair
migration starts from) must not become unusable as an `apply` target because
"non-conformant candidate ⇒ abort" fires on a pre-existing error the
mutation never touched. So `apply --write` does not touch the real bundle
until a *baseline-relative* validation of the whole candidate bundle has
passed, and the source it validated is proven unchanged before anything is
replaced:

1. run `check` against the real bundle first and record its diagnostics as
   the baseline — this is what makes "creates nonconformance" a comparison,
   not an absolute bar;
2. read every document `apply` will touch and record a content hash
   (`sha256`) per path alongside its real bytes, then render candidate bytes
   for each (round-trip YAML write, step 3) in memory — nothing hits disk
   yet;
3. materialize a candidate bundle tree: touched documents get their
   candidate bytes; every other document, including `.okfignore` and
   reserved files (`index.md`, `log.md`), is **hardlinked** from the real
   tree, with a full-copy fallback when hardlinking isn't available (a
   different filesystem, a platform without hardlink support) — not
   symlinked. `discover_markdown` runs with `follow_symlinks=False`
   deliberately, so a symlinked candidate tree would have the untouched
   majority of the bundle silently invisible to `check`, making the "whole
   candidate bundle" guarantee this step exists for false. For every
   hardlinked path, also record `(size, mtime_ns)` from the real tree at
   link time — this, not a content hash, is what step 6 re-checks for the
   untouched majority, since re-hashing every file's *content* on a
   45,705-document bundle to validate a mutation that touched a handful of
   them is not a cost this design should default to paying;
4. run `check` against that candidate tree — this is what makes cross-concept
   validation (broken links, uniqueness) see the mutation's full effect, not
   just the touched files in isolation;
5. compare the candidate's diagnostics against the baseline from step 1 by a
   stable key (code, path, message); if the candidate has any **normative**
   diagnostic absent from the baseline, write nothing, leave the real bundle
   untouched, and exit non-zero naming the newly introduced diagnostics — a
   baseline diagnostic that survives unchanged, or that the mutation actually
   fixed, is not a reason to abort;
6. immediately before replacing anything, re-check **every path validation
   considered**, not only the touched ones: re-hash each touched path and
   compare against the step 2 snapshot; re-stat every hardlinked path and
   compare `(size, mtime_ns)` against the step 3 snapshot, including paths
   that did not exist as concepts before (a new document added to the real
   tree after step 1 changes what `check` would see, even though it is
   neither touched nor hardlinked) — a full re-run of `discover_markdown`'s
   listing against the step 1 listing catches additions and deletions the
   per-path checks above cannot. If anything in that comparison differs,
   another process or a human changed the bundle after `apply` validated
   it — abort with nothing written, naming every changed, added, or removed
   path, rather than write against a state that no longer matches what was
   actually validated;
7. only if steps 5 and 6 both hold, replace each touched document: write its
   candidate bytes to an adjacent temporary file on the same filesystem, then
   atomically rename it over the original (`os.replace`), so a crash
   mid-write cannot corrupt that file.

Step 6's `(size, mtime_ns)` check for untouched files is a weaker guarantee
than the content hash used for touched ones — a same-second, same-size
in-place edit could in principle evade it, the same accepted limitation
tools like `rsync` and `make` carry. A future stricter mode could re-hash
everything at the cost of reading the full bundle twice per invocation; this
design does not default to that cost. Step 7 is also not a cross-file
transaction — a crash between two file replacements in a large batch can
still leave a batch partially applied — but by that point every individual
replacement is atomic and was validated against a source state step 6 just
confirmed still matches disk. Deferred cross-file atomicity is discussed
under "Cross-file atomicity for the final replace pass" below; it is a
materially smaller failure mode than the ones steps 1–6 close, which is why
it can stay deferred while those cannot.

### 6. Idempotency: bounded to what `apply` controls, not to the caller's SQL

Running the same `apply` invocation twice must introduce **no diff beyond
what the SQL's own output specifies** — `apply`'s own machinery (staging,
diffing, round-trip serialization) never manufactures a second-run diff by
itself. That is a real, enforceable guarantee, backed by conformance
fixtures.

It is not the same claim as "the invocation is idempotent" in general, and
the RFC no longer makes that broader claim: `SET counter = counter + 1`,
`SET value = value || 'x'`, or `SET updated_at = current_timestamp` change on
every run by construction, and no amount of row-diffing on `apply`'s side can
make a caller's non-convergent mutation convergent. All of this RFC's own
examples (equality and `IS NULL` guards) are idempotent by the caller's
choice, not by a guarantee `apply` enforces. `apply` best-effort warns — but
does not block — when `--sql` contains an obviously non-deterministic
construct (`current_timestamp`, `now()`, `random()`), since those are the
common way an author accidentally defeats their own `WHERE` guard.

Nor does `apply` promise that literally *rerunning* a script that already
succeeded will run at all, let alone as a no-op: 1b's type-rename example
hits a "table does not exist" error on a converged rerun, and a script whose
leading `ALTER TABLE ... ADD COLUMN` already ran once will hit DuckDB's own
"column already exists" error the second time. Both are honest failures
from DuckDB's own catalog, surfaced as-is, not something `apply` smooths
over — the guarantee above is about what `apply` itself never adds to a
diff, not about a script being safe to blindly rerun.

### CLI and MCP surface

- `okf-parser apply PATH --sql "..." [--write] [--exclude PATTERN]...`, where
  `--sql` is zero or more leading `ALTER TABLE` statements plus one trailing
  `UPDATE` (decision 1a), and `PATH --type TYPE --field FIELD --from ... --to ...`
  as the sugar form, where `--type` is required only there, since the sugar
  has no SQL statement to read a target identifier out of — both dry-run by
  default;
- no MCP tool: consistent with `duckdb`, every other write operation is
  CLI-only because the MCP surface is read-only by design (see `docs/cli.md`);
- output payload shape mirrors `format`'s `{"changed_paths", "skipped_paths", "succeeded", "written"}`, plus `apply`-specific keys: `validation` (the
  newly introduced diagnostics from decision 5, step 5, empty on success) and
  `conflict_paths` (paths that failed the step 6 re-check — changed, added, or
  removed — empty unless a concurrent edit aborted the write).

## Alternatives considered

### Reserialize frontmatter wholesale with `pyyaml`

Rejected. `pyyaml`'s dumper does not preserve comments, key order, or
formatting quirks a human author relied on; every write would look like a
full-file diff instead of the one or two lines that actually changed, which
defeats the "safe to run against a human-authored bundle" requirement in the
issue.

### Give every command a general `--set field=value` flag instead of SQL

Rejected as the primary surface, kept as sugar. The issue's own reasoning
holds: a bespoke flag language for selection re-implements `WHERE`, and the
relation already exists in DuckDB. SQL's cost is a dependency the project
already has (`duckdb`, `ibis-framework[duckdb]`).

### Mutate `bundle.concepts` in place without a staging table

Rejected. `frontmatter_json` as a single JSON column forces every mutation
into `json_extract`/`json_set` expressions, which is exactly the SQL
ergonomics the issue explicitly wanted to avoid by choosing
`UPDATE ... SET field = ...` over a JSON-path DSL. A single bundle-wide table
was also considered and rejected for the field-name-reconciliation reasons
above ("Why the concept relation cannot be `UPDATE`d directly — and why one
table isn't the fix either").

### Name the staging table by the type's slug

Rejected — this was this RFC's second draft. `type_slug()` is not injective:
the motivating pair `"Revisao Ciencia"` / `"Revisão Ciência"` both slug to
`revisao-ciencia` because the function strips accents by design, so a table
name derived from it cannot reliably select exactly one producer-defined
type. A collision guard on the slug would at best turn a silent wrong-type
selection into an error; it cannot recover the injectivity the design needs,
and it would actively break the exact partial-migration case where old and
new type values legitimately coexist mid-rename.

### Decouple type identity into a required `--type` flag, fixed table name `concepts`

Rejected — this was this RFC's third draft, and it worked, but review
pointed out it was solving a problem that did not need solving: DuckDB
supports quoted Unicode identifiers, so there was no reason to avoid using
the authored `type` value as the table name directly. Keeping `--type` as a
separate flag beside `--sql` meant two places could disagree about which
type was meant (they never structurally could in this draft, since nothing
in the SQL referenced type — but it was still one more piece of state to
keep in sync for no benefit over reading it straight out of the `UPDATE`
statement).

### Have `apply` parse `--sql` to extract the target identifier and synthesize new columns

Rejected — this was this RFC's fourth draft, materializing only the one type
named by a hand-rolled extraction of the `UPDATE` target (unescaping quoted
identifiers, checking case-insensitive collisions against every other type)
and auto-creating a `VARCHAR` column for any unrecognized `SET` target.
Review's point: `okf-parser` re-implementing slices of what DuckDB's own
parser, binder, and catalog already do — Unicode identifier resolution,
case-folding, "does this column exist" — is duplicated, error-prone logic
for no benefit DuckDB doesn't already provide. Materializing every type
up front and handing the script to DuckDB mostly as-is (decision 1),
bounding its *shape* via `extract_statements()` instead of its identifiers
(decision 1a), and discovering what changed from a diff instead of a
pre-declared target (decision 2) replaces custom parsing with DuckDB doing
the parsing it was already going to do internally regardless.

### Allow arbitrary DuckDB SQL, not a bounded `ALTER TABLE` + `UPDATE` script

Rejected. Arbitrary SQL could run multiple `UPDATE`s or `DELETE`/`INSERT`
statements with independent side effects the diff in step 2 was not built to
attribute to one bounded operation, or DDL that reshapes something other
than the three `ALTER TABLE` forms 1c's scalar-only contract can validate.
Bounding `--sql` to leading `ALTER TABLE ADD/DROP/RENAME COLUMN` statements
plus one trailing `UPDATE` (1a) is deliberately generous enough to cover
every motivating case in this RFC without opening the door to statements
decision 2's diff and decision 4's post-execution checks were not designed
to reason about.

### Cross-file atomicity for the final replace pass

Considered and deferred, on a narrower question than before decision 5 had a
concurrency check: per-document atomicity (temp-file-then-rename, step 7) and
the pre-replacement re-hash (step 6) together mean every individual
replacement is both internally atomic and validated against source state
just confirmed current. What is still deferred is a crash **between** two
file replacements in a large batch, which could leave a batch of
already-validated, individually-atomic writes partially applied. A manifest
of pending replacements resumable after a crash would close that gap;
deferred because a partial-but-individually-valid write, resumed by rerunning
`apply` with a `WHERE` clause the caller wrote to be convergent (decision 6
only promises `apply`'s own machinery adds no incidental diff, not that any
script is safe to blindly rerun), is a materially smaller failure mode than
the ones decision 5 now closes: publishing an invalid bundle, or
overwriting an edit `apply` never saw.

## Open questions

- Whether v1's type inference (1c: top-level scalars, `VARCHAR`, lexical
  spelling) should later widen to reuse `schema_contract.py`'s full inferred
  typing (`int`, `bool`, `date`) for `UPDATE` ergonomics, once structured
  (list/map) writes are in scope and there is a real caller need to weigh
  against the ambiguity that richer typing reintroduces.
- Benchmarked cost of the hardlink-based candidate tree (decision 5, step 3)
  on a bundle the size that motivated #26 (45,705 documents): hardlinking is
  cheap in principle on a POSIX filesystem, but this needs measuring, not
  assuming, before the design is accepted.
- Benchmarked cost of materializing **every** distinct type as a table
  (decision 1), not only the one a script touches, on that same bundle. This
  RFC deliberately chose implementation simplicity (let DuckDB resolve
  identifiers, no custom parsing) over the narrower "materialize one type,
  lazily" cost bound an earlier draft had; whether that trade is still
  acceptable at 45,705 documents needs the same measurement the hardlink
  question does, not an assumption either way.
- 1b (table rename semantics for `type`) is still genuinely undecided and is
  what keeps this RFC at `proposed`; everything else above is a stated
  decision, not an open question.

Richer, spec-declared typing and documentation (real DuckDB column types
and `COMMENT ON` metadata sourced from an optional `.okf/specs` document,
with `ALTER TABLE` writing back to the spec that declared a type) is
deliberately not designed here — it is large enough on its own, with its
own round-trip and validation needs for a second document, to warrant its
own proposal. See RFC 0006, which depends on this RFC's `ALTER TABLE`
mechanism and should not be accepted before it.

`apply` ships Python-first. TypeScript parity is explicitly not a
precondition for accepting or implementing this RFC: the TypeScript package
has no native DuckDB dependency outside the separate `typescript-duckdb`
adapter, and porting `apply` is follow-on work tracked separately once the
Python implementation and its conformance fixtures exist to port against.
