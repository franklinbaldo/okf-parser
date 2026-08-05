---
type: RFC
title: Relational writes to frontmatter fields
status: proposed
description: Define an `apply` command that mutates frontmatter fields through one parsed SQL UPDATE against an exact-type-selected staging table, with round-trip fidelity, dry-run, and stage-then-validate-then-write semantics
---

# RFC 0005: Relational writes to frontmatter fields

## Summary

Add `okf-parser apply PATH --type "Rotina" --sql "UPDATE concepts SET ... WHERE ..."`,
a command that mutates frontmatter fields in place across a bundle. `apply`
materializes exactly one concept type at a time — selected by `--type`, an
exact match against the authored `type` field, not a derived slug — into a
fixed-name staging table, `concepts`. Internally this is "one table per
type" in spirit: each invocation's table holds only the fields of the one
type it was asked for, mirroring how `schema_contract.py` already compiles
one `TypeContract` per type rather than reconciling every type's fields
into one shape. `--type` is what stays exact; the table name does not carry
type identity at all, for reasons the next section explains.

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

**Materializing one type at a time** removes the reconciliation instead of
managing it — each invocation's staging table only ever holds one type's
fields, mirroring `schema_contract.py`'s one-`TypeContract`-per-type
discovery. A second draft named that table by the type's slug (the same
function `type_specs.py` derives type-spec document paths with) so
`UPDATE rotina SET ...` would select "the `Rotina` type" by naming its
table. That is broken: **`type_slug()` is not injective.** This RFC's own
motivating example is the counterexample — `"Revisao Ciencia"` and
`"Revisão Ciência"` both slug to `revisao-ciencia`, because the slug strips
accents by design (`changelog/0.14.0.md`). Two distinct producer-defined
types can collide on punctuation or case the same way, and a type name in a
script other than Latin can slug to an empty string. A table name derived
from a lossy function cannot reliably select exactly one type, which is the
one thing decision 1a below needs it to do.

So type selection and the SQL table identifier are two different things.
`apply` takes `--type TYPE` as a required, separate flag — an **exact**
string match against the authored `type` field, no slugging, no
normalization — and always exposes the materialized result to `--sql` under
one fixed, stable name: `concepts`. The table name carries no type identity
to collide on; `--type` is the only thing that does, and it is compared
exactly.

Within the materialized table, the writable namespace is the selected
type's authored frontmatter keys, each exactly once, plus filesystem-derived
identity under a reserved `__okf_*` prefix (`__okf_path`, `__okf_concept_id`,
`__okf_logical_key`) that cannot collide with an authored key. `apply`
refuses to run against any type where an observed key already starts with
`__okf_`, rather than silently shadowing it. The table's own name needs no
such reservation — nothing in frontmatter can become a SQL table identifier —
which is exactly why fixing it to `concepts` costs nothing.

The `__okf_` prefix is not the only collision to guard: DuckDB identifiers
are **case-insensitive**, quoted or not, so two distinct YAML keys that only
differ by case — `setor` and `Setor` — cannot become two separate writable
columns; DuckDB would treat a reference to either as the same identifier.
Quoting handles spaces, punctuation, and reserved words in a key (any string
is a valid quoted identifier), so that part needs no special handling.
Case-folding does. Before compiling a type's table, `apply` checks every
observed key of that type for a collision under DuckDB's identifier equality
(case-insensitive comparison) and refuses to compile the table if any two
keys collide, naming both — deduplicating automatically was rejected because
which of the two keys silently "wins" the column would depend on scan order,
and a writeback that depends on scan order is not deterministic.

A concept with no `type` at all, or a reserved document (`index.md`,
`log.md`; see `RESERVED_FILENAMES` in `parser.py`), matches no `--type` value
and `apply` cannot target it; `check`/`inventory` remain where an untyped
concept is visible.

## Decision

### 1. `--type` selects, `apply` compiles one staging table named `concepts`

DuckDB's `UPDATE` only targets tables, not views — `UPDATE v1 ...` against a
view is a binder error. `--type TYPE` is required by every `--sql` and
`--field` invocation; `apply` never materializes the whole bundle. It filters
concepts to an **exact** match on `TYPE` first, then builds one **temporary
table** (`CREATE TEMP TABLE concepts AS ...`) from exactly that selection,
unnesting `frontmatter_json` into one column per authored key observed on
the selected concepts, using `schema_contract.py`'s existing per-type
discovery. This is also what keeps the cost bounded on a large bundle: a
mutation scoped to one type never touches the concepts of any other.

Zero concepts currently matching `--type` is not an error — `concepts` is
compiled empty, and the caller's `UPDATE` legitimately affects zero rows.
This is a deliberate choice, not an oversight: it is what makes rerunning a
migration after it has already converged (decision 6) a no-op instead of a
failure, at the cost of `apply` being unable to distinguish "this type
genuinely has nothing left to migrate" from "the caller mistyped `--type`."
Dry-run mitigates the mistyped case — a `--write`-less run reports zero
candidates either way, so a caller checking before writing sees it.

A second, untouched copy of the same rows — an internal `_before` pair, never
exposed to the caller's SQL — is materialized alongside it, so step 2 has an
immutable pre-image to diff against regardless of what the mutation does.

The caller's `UPDATE concepts SET ... WHERE ...` runs against the staging
table for the selected type. This is what makes the issue's examples work,
scoped to one type instead of the whole bundle:

```bash
okf-parser apply ./bundle --type "Rotina" --sql "
  UPDATE concepts
     SET setor = '#GAB#FSB'
   WHERE setor IS NULL AND __okf_path LIKE 'rotinas/%'
" --write
```

A simpler surface without `WHERE` is also offered for the common case, so a
caller who only needs an unconditional rename does not need to write SQL —
though see 1b for why a rename of `type` itself is not yet a settled part of
this design:

```bash
okf-parser apply ./bundle --type "Rotina" --field setor --from "GAB" --to "#GAB#FSB"
```

`--type` is required here too — with the table always named `concepts`,
nothing else in the invocation identifies which type's `setor` is meant, and
several types can plausibly have a field with that name.
`--field/--from/--to` is sugar for a one-column
`UPDATE concepts WHERE field = from`; it exists because the SQL form makes
the trivial case verbose, not because selection needs a second
implementation.

### 1a. `--sql` accepts exactly one parsed `UPDATE` against `concepts`, nothing else

`apply` parses `--sql` before executing anything and rejects it unless it is
a single `UPDATE` statement targeting exactly `concepts`: no semicolon-
separated second statement, no DDL, no `DELETE`/`INSERT`, no statement
naming any other table. This is not a stylistic preference — decision 4's
target-column validation and step 2's bounded diff both assume the mutation
is exactly one `UPDATE` against the one table `apply` compiled; arbitrary SQL
could touch unrelated tables or run side effects the diff never sees. The
parse step is what makes that assumption an enforced contract instead of a
hope. Because the table name is always `concepts`, this check is a fixed
string comparison, not a lookup against a set of known type slugs — there is
no such set to be wrong about.

### 1b. Undecided: what happens when the mutation changes `type` itself

Motivation case 2 above — renaming `"Revisao Ciencia"` to `"Revisão Ciência"`
in bulk — is a `type` rewrite. Since `--type` is now an exact pre-filter
rather than a table name, `type` inside `concepts` is, mechanically, an
ordinary column like any other — the open question is whether `apply` lets
`SET` touch it at all. This RFC does not yet pick between two ways to
handle it, and is not marked `accepted` until it does:

- **Let it migrate.**

  ```bash
  okf-parser apply ./bundle --type "Revisao Ciencia" --sql "
    UPDATE concepts SET type = 'Revisão Ciência'
  " --write
  ```

  `--type` already selected every row with the exact old value, so the
  `UPDATE` needs no `WHERE` at all for the pure-rename case. At write time
  (decision 5), `apply` determines each document's destination purely by its
  post-mutation `type` value, independent of what `--type` was for this
  invocation — this falls out of diffing by `__okf_concept_id`, not a new
  mechanism. `check` (already run against the full candidate bundle in
  decision 5) is what would catch a destination type without a matching
  spec, if `--require-spec` is in play.

  This is also what makes decision 1's "zero rows is a valid, successful
  no-op" rule load-bearing rather than incidental: the *first* run of the
  invocation above selects every concept whose exact `type` is
  `"Revisao Ciencia"` and migrates it. Running the identical invocation a
  second time selects **zero** concepts — none have that exact `type` any
  more — compiles an empty `concepts` table, and the `UPDATE` affects
  nothing. A true no-op, without `apply` doing anything extra to guarantee
  it, and without ever hitting the "unknown table" failure a slug-named table
  would have hit here (the blocker that ruled out the slug-based design
  above).

- **Forbid it.** `type` becomes read-only inside `apply`, like `__okf_path`.
  Bulk type rename gets a dedicated command
  (`okf-parser rename-type FROM TO`, out of this RFC's scope) instead of
  being one case of a general `UPDATE`.

The rest of this RFC is written assuming `type` behaves like any other
column unless a table migration is explicitly not possible for some other
reason surfaced during review; §6 (idempotency) and the write path in
decision 5 do not change under either choice, only what `apply` accepts.

### 1c. v1 scope: top-level scalars only, explicit presence, explicit null contract

"One column per authored key observed anywhere on concepts of that type"
(decision 1) leaves three things undefined that must not stay implicit:

- **A column for a field no selected document has yet.** Backfilling
  (motivation case 3) means `SET timestamp = ...` has to bind even when no
  document being touched has a `timestamp` key at all. `apply` parses every
  name in the `--sql` statement's `SET` list (the same parse step as 1a) and
  creates a `VARCHAR` column, `NULL` by default, for any target not already
  discovered — a mutation is allowed to introduce a field, not only edit one.
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
  any selected document is excluded from the table entirely; `SET` naming it
  is rejected at the 1a parse step, the same way a `__okf_*` target is.
  Structured writes are real future work, not silently unsupported.

### 2. Diff before write, one document at a time

After the `UPDATE` runs, `apply` diffs each staged row against its `_before`
counterpart per `__okf_concept_id`. Only documents with at least one changed
column (or, if 1b resolves to "let it migrate," a changed `type`) are
candidates for writing. The diff, not the SQL statement, is what a
`--write`-less run reports — this keeps the dry-run output meaningful even
when the mutation's `WHERE` clause matches nothing.

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

### 4. Refuse non-writable columns

`__okf_path`, `__okf_concept_id`, and `__okf_logical_key` are derived from
the filesystem, not authored. `SET __okf_path = ...` is a rename, a different
and unimplemented operation. The `--sql` parse step (1a) rejects any `UPDATE`
whose target list includes a `__okf_*` column, before running anything, with
an error naming the column — the caller's SQL never gets a chance to touch
filesystem identity.

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

### CLI and MCP surface

- `okf-parser apply PATH --type TYPE --sql "..." [--write] [--exclude PATTERN]...`
  and the `--type TYPE --field FIELD --from ... --to ...` sugar, both dry-run
  by default; `--type` is required by both forms — it is the only thing that
  selects which type `concepts` is compiled from;
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

Rejected — this was this RFC's own second draft, and review caught that it
does not work. `type_slug()` is not injective: the motivating pair
`"Revisao Ciencia"` / `"Revisão Ciência"` both slug to `revisao-ciencia`
because the function strips accents by design, so a table name derived from
it cannot reliably select exactly one producer-defined type. A collision
guard on the slug would at best turn a silent wrong-type selection into an
error; it cannot recover the injectivity the design needs, and it would
actively break the exact partial-migration case where old and new type
values legitimately coexist mid-rename. Decoupling type identity
(`--type`, exact match) from the SQL table identifier (always `concepts`)
keeps the one thing that draft got right — materialize only the one type
being mutated — without relying on a lossy function to do selection.

### Allow arbitrary DuckDB SQL, not just one parsed `UPDATE`

Rejected. Arbitrary SQL could target a table other than `concepts`, run
multiple statements with independent side effects, or use DDL — none of
which the diff in step 2 or the target-column check in 1a/4 is built to
bound. Restricting `--sql` to one parsed `UPDATE` against the one designated
staging table is what makes those two guarantees actual guarantees instead
of best-effort.

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
`apply` (idempotent per decision 6), is a materially smaller failure mode
than the ones decision 5 now closes: publishing an invalid bundle, or
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
- 1b (table rename semantics for `type`) is still genuinely undecided and is
  what keeps this RFC at `proposed`; everything else above is a stated
  decision, not an open question.

`apply` ships Python-first. TypeScript parity is explicitly not a
precondition for accepting or implementing this RFC: the TypeScript package
has no native DuckDB dependency outside the separate `typescript-duckdb`
adapter, and porting `apply` is follow-on work tracked separately once the
Python implementation and its conformance fixtures exist to port against.
