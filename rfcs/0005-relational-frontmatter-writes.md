---
type: RFC
title: Relational writes to frontmatter fields
status: proposed
description: Define an `apply` command that mutates frontmatter fields through one parsed SQL UPDATE against a per-type staging table, with round-trip fidelity, dry-run, and stage-then-validate-then-write semantics
---

# RFC 0005: Relational writes to frontmatter fields

## Summary

Add `okf-parser apply PATH --sql "UPDATE rotina SET ... WHERE ..."`, a
command that mutates frontmatter fields in place across a bundle. A bundle is
modeled as **one table per concept type** — `rotina`, `revisao-ciencia`, and
so on — rather than one table spanning every type, because that is already
how the rest of the tool understands a bundle: `schema_contract.py` compiles
one `TypeContract` per type, and a type's fields have never been assumed
comparable to another type's fields with the same name.

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

**One table per type** removes the reconciliation instead of managing it.
Each type already has independent field discovery in `schema_contract.py`
(one `TypeContract` per type); `apply` reuses exactly that grouping instead
of a bundle-wide union. A bundle materializes as `rotina`, `revisao-ciencia`,
and so on — one table per type, named by the same slug function
`type_specs.py` already derives type-spec document paths with, quoted where
the slug needs it (DuckDB identifiers with a `-` need quoting: `"revisao- ciencia"`).

Within one type's table, the writable namespace is that type's authored
frontmatter keys, each exactly once, plus filesystem-derived identity under a
reserved `__okf_*` prefix (`__okf_path`, `__okf_concept_id`,
`__okf_logical_key`) that cannot collide with an authored key. `apply`
refuses to run against any type where an observed key already starts with
`__okf_`, rather than silently shadowing it. The table's own name needs no
such reservation — nothing in frontmatter can become a SQL table identifier —
so it is named plainly.

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
`log.md`; see `RESERVED_FILENAMES` in `parser.py`), belongs to no per-type
table and `apply` cannot target it; `check`/`inventory` remain where an
untyped concept is visible.

## Decision

### 1. Compile one staging table per type, lazily

DuckDB's `UPDATE` only targets tables, not views — `UPDATE v1 ...` against a
view is a binder error. `apply` never materializes the whole bundle: the
`--sql` statement's target table name *is* the type selector, so `apply`
parses it first (1a), then builds exactly one **temporary table**
(`CREATE TEMP TABLE rotina AS ...`) for that type, unnesting
`frontmatter_json` into one column per authored key observed on concepts of
that type, using `schema_contract.py`'s existing per-type discovery. This is
also what keeps the cost bounded on a large bundle: a mutation scoped to one
type never touches the concepts of any other.

A second, untouched copy of the same rows — an internal `_before` pair, never
exposed to the caller's SQL — is materialized alongside it, so step 2 has an
immutable pre-image to diff against regardless of what the mutation does.

The caller's `UPDATE rotina SET ... WHERE ...` runs against the staging
table for that type. This is what makes the issue's examples work, scoped to
one type instead of the whole bundle:

```bash
okf-parser apply ./bundle --sql "
  UPDATE rotina
     SET setor = '#GAB#FSB'
   WHERE setor IS NULL AND __okf_path LIKE 'rotinas/%'
" --write
```

A simpler surface without `WHERE` is also offered for the common case, so a
caller who only needs unconditional rename does not need to write SQL —
though see 1b for why a rename of `type` itself is not yet a settled part of
this design:

```bash
okf-parser apply ./bundle --field setor --from "GAB" --to "#GAB#FSB"
```

`--field/--from/--to` is sugar for a one-column `UPDATE ... WHERE field = from`; it exists because the SQL form makes the trivial case verbose, not
because selection needs a second implementation.

### 1a. `--sql` accepts exactly one parsed `UPDATE`, nothing else

`apply` parses `--sql` before executing anything and rejects it unless it is
a single `UPDATE` statement targeting exactly one table: no semicolon-
separated second statement, no DDL, no `DELETE`/`INSERT`, no statement naming
more than one table. The parsed target's name is what selects which type's
staging table gets built at all (step 1) — an unrecognized table name (no
type slugs to it) is rejected with the list of known type slugs, before
anything is materialized. This is not a stylistic preference — step 4's
target-column validation and step 2's bounded diff both assume the mutation
is exactly one `UPDATE` against one designated table; arbitrary SQL could
touch unrelated tables or run side effects the diff never sees. The parse
step is what makes that assumption an enforced contract instead of a hope.

### 1b. Undecided: what happens when the mutation changes `type` itself

Motivation case 2 above — renaming `"Revisao Ciencia"` to `"Revisão Ciência"`
in bulk — is a `type` rewrite, and under a per-type-table model that is not
an ordinary column update: it is a **table rename with a `WHERE` clause**,
`UPDATE rotina SET type = 'Revisão Ciência' WHERE type = 'Revisao Ciencia'`
read as "move every row here matching the old name into the table the new
name maps to." This RFC does not yet pick between two ways to handle it, and
is not marked `accepted` until it does:

- **Let it migrate.** `type` stays an ordinary, rewritable column inside its
  table. At write time (decision 5), `apply` determines each document's
  destination purely by its post-mutation `type` value, independent of which
  table it was staged in — this falls out of diffing by `__okf_concept_id`
  rather than by table membership, not a new mechanism. `check` (already run
  against the full candidate bundle in decision 5) is what would catch a
  destination type without a matching spec, if `--require-spec` is in play.

  Reading it as a table rename also settles idempotency (decision 6) for
  free: the *first* run's `WHERE type = 'Revisao Ciencia'` matches the rows
  that still carry the old name and migrates them out. Running the identical
  invocation a second time matches nothing — those rows no longer have that
  `type` — so it is a true no-op without `apply` doing anything extra to
  guarantee it. A migration command is naturally self-limiting in exactly the
  way §6 wants every mutation to be.

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
   candidate bytes: every other document, including `.okfignore` and
   reserved files (`index.md`, `log.md`), is **hardlinked** from the real
   tree, with a full-copy fallback when hardlinking isn't available (a
   different filesystem, a platform without hardlink support) — not
   symlinked. `discover_markdown` runs with `follow_symlinks=False`
   deliberately, so a symlinked candidate tree would have the untouched
   majority of the bundle silently invisible to `check`, making the "whole
   candidate bundle" guarantee this step exists for false;
4. run `check` against that candidate tree — this is what makes cross-concept
   validation (broken links, uniqueness) see the mutation's full effect, not
   just the touched files in isolation;
5. compare the candidate's diagnostics against the baseline from step 1 by a
   stable key (code, path, message); if the candidate has any **normative**
   diagnostic absent from the baseline, write nothing, leave the real bundle
   untouched, and exit non-zero naming the newly introduced diagnostics — a
   baseline diagnostic that survives unchanged, or that the mutation actually
   fixed, is not a reason to abort;
6. immediately before replacing anything, re-hash every touched path on disk
   and compare against the snapshot from step 2; if any differs, another
   process or a human edited that file after `apply` read it and before it
   was about to write — abort with nothing written, naming the changed
   paths, rather than overwrite a change `apply` never validated against;
7. only if steps 5 and 6 both hold, replace each touched document: write its
   candidate bytes to an adjacent temporary file on the same filesystem, then
   atomically rename it over the original (`os.replace`), so a crash
   mid-write cannot corrupt that file.

Step 7 is not a cross-file transaction — a crash between two file
replacements in a large batch can still leave a batch partially applied —
but by that point every individual replacement is atomic and was validated
against a source state step 6 just confirmed still matches disk. Deferred
cross-file atomicity is discussed under "Atomic replace across every touched
file" below; it is a materially smaller failure mode than the ones steps 1–6
close, which is why it can stay deferred while those cannot.

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

- `okf-parser apply PATH --sql "..." [--write] [--exclude PATTERN]...` and the
  `--field/--from/--to` sugar, both dry-run by default;
- no MCP tool: consistent with `duckdb`, every other write operation is
  CLI-only because the MCP surface is read-only by design (see `docs/cli.md`);
- output payload shape mirrors `format`'s `{"changed_paths", "skipped_paths", "succeeded", "written"}`, plus `apply`-specific keys: `validation` (the
  newly introduced diagnostics from decision 5, step 5, empty on success) and
  `conflict_paths` (paths that failed the step 6 re-hash, empty unless a
  concurrent edit aborted the write).

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

### Mutate `bundle.concepts` in place without a per-type staging table

Rejected. `frontmatter_json` as a single JSON column forces every mutation
into `json_extract`/`json_set` expressions, which is exactly the SQL
ergonomics the issue explicitly wanted to avoid by choosing
`UPDATE ... SET field = ...` over a JSON-path DSL. A single bundle-wide table
was also considered and rejected for the field-name-reconciliation reasons
above ("Why the concept relation cannot be `UPDATE`d directly — and why one
table isn't the fix either").

### Allow arbitrary DuckDB SQL, not just one parsed `UPDATE`

Rejected. Arbitrary SQL could target a table other than the one type's table
the parsed statement names, run multiple statements with independent side
effects, or use DDL — none of which the diff in step 2 or the target-column
check in 1a/4 is built to bound. Restricting `--sql` to one parsed `UPDATE`
against the one designated staging table is what makes those two guarantees
actual guarantees instead of best-effort.

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
