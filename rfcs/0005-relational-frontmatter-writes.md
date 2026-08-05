---
type: RFC
title: Relational writes to frontmatter fields
status: proposed
description: Define an `apply` command that mutates frontmatter fields through one parsed SQL UPDATE against a flattened staging table, with round-trip fidelity, dry-run, and stage-then-validate-then-write semantics
---

# RFC 0005: Relational writes to frontmatter fields

## Summary

Add `okf-parser apply PATH --sql "UPDATE __okf_staging SET ... WHERE ..."`, a
command that mutates frontmatter fields in place across a bundle. It reuses
the existing relational compilation (`duckdb`, `schema`) to select and
express the mutation, but writes back through a purpose-built round-trip YAML
loader instead of re-serializing frontmatter wholesale — a new capability,
not one borrowed from `format`, which never touches frontmatter content at
all (`mdformat-frontmatter` treats it as an opaque, passed-through block).
`apply` does adopt `format`'s "refuse rather than risk a bad rewrite" posture
— skip and report a document instead of writing something lossy — and its
dry-run-by-default convention.

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

## Why the concept relation cannot be `UPDATE`d directly

`bundle.concepts` (`bundle.py`) has one column per *promoted* field
(`concept_type`, `title`, `description`) and a single `frontmatter_json`
column — but `frontmatter_json` already serializes `self.frontmatter`
wholesale (`models.py`), so it *already contains* `type`, `title` and
`description`. Flattening it naively and keeping the promoted columns
alongside produces two names for the same value (`title` twice,
`concept_type` next to `type`) and no name at all for `setor`.

The materialized schema needs exactly one writable namespace — the authored
frontmatter keys, each exactly once — plus filesystem-derived identity
exposed under names that cannot collide with an authored key. Reserving a
prefix does that: `__okf_path`, `__okf_concept_id`, `__okf_logical_key`.
`apply` refuses to run against any bundle where an observed frontmatter key
already starts with `__okf_`, rather than silently shadowing it.

So `apply` cannot run the caller's SQL against `bundle.concepts` as-is. It
must compile a differently-shaped staging relation first.

## Decision

### 1. Compile a flattened staging table per invocation

DuckDB's `UPDATE` only targets tables, not views — `UPDATE v1 ...` against a
view is a binder error. Before running the caller's SQL, `apply` builds a
**temporary table** (`CREATE TEMP TABLE __okf_staging AS ...`) that unnests
`frontmatter_json` into one column per authored key observed anywhere in the
selected concepts, using the same key/type discovery `schema_contract.py`
performs for `schema --infer-types`, applied here to shape columns instead of
a JSON Schema. Filesystem identity is included under the `__okf_*` names
above; there are no separate "promoted" columns.

A second, untouched copy of the same rows — `__okf_before`, never exposed to
the caller's SQL — is materialized alongside it, so step 2 has an immutable
pre-image to diff against regardless of what the mutation does.

The caller's `UPDATE __okf_staging SET ... WHERE ...` runs against the
staging table. This is what makes the issue's examples work as written,
modulo the table name:

```bash
okf-parser apply ./bundle --sql "
  UPDATE __okf_staging
     SET setor = '#GAB#FSB'
   WHERE setor IS NULL AND __okf_path LIKE 'rotinas/%'
" --write
```

A simpler surface without `WHERE` is also offered for the common case, so a
caller who only needs unconditional rename does not need to write SQL:

```bash
okf-parser apply ./bundle --field type --from "Revisao Ciencia" --to "Revisão Ciência"
```

`--field/--from/--to` is sugar for a one-column `UPDATE ... WHERE field = from`; it exists because the SQL form makes the trivial case verbose, not
because selection needs a second implementation.

### 1a. `--sql` accepts exactly one parsed `UPDATE`, nothing else

`apply` parses `--sql` before executing anything and rejects it unless it is
a single `UPDATE` statement whose target is `__okf_staging`: no semicolon-
separated second statement, no DDL, no `DELETE`/`INSERT`, no statement naming
a different table. This is not a stylistic preference — step 1a's target-
column validation (below) and step 2's bounded diff both assume the mutation
is exactly one `UPDATE` against one designated table; arbitrary SQL could
touch unrelated tables or run side effects the diff never sees. The parse
step is what makes that assumption an enforced contract instead of a hope.

### 2. Diff before write, one document at a time

After the `UPDATE` runs, `apply` diffs each `__okf_staging` row against its
`__okf_before` counterpart per `__okf_concept_id`. Only documents with at
least one changed column are candidates for writing. The diff, not the SQL
statement, is what a `--write`-less run reports — this keeps the dry-run
output meaningful even when the mutation's `WHERE` clause matches nothing.

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

If the round-trip loader cannot parse a document's frontmatter losslessly
(an anchor, a merge key, a construct `ruamel.yaml` cannot round-trip), `apply`
skips that document and reports it — mirroring `format`'s `skipped_paths` for
documents whose protected block structure a rewrite would change — rather
than falling back to a lossy rewrite.

### 4. Refuse non-writable columns

`__okf_path`, `__okf_concept_id`, and `__okf_logical_key` are derived from
the filesystem, not authored. `SET __okf_path = ...` is a rename, a different
and unimplemented operation. The `--sql` parse step (1a) rejects any `UPDATE`
whose target list includes a `__okf_*` column, before running anything, with
an error naming the column — the caller's SQL never gets a chance to touch
filesystem identity.

### 5. Validate before writing: stage, validate, then replace

Issue #26 requires aborting when a mutation creates nonconformance, not
reporting it after the fact — a large bundle must never end up partially
written and invalid because `apply` found out too late. So `apply --write`
does not touch the real bundle until validation of the *whole* candidate
bundle has passed:

1. render candidate bytes for every touched document (round-trip YAML write,
   step 3) in memory — nothing hits disk yet;
2. materialize a candidate bundle view: touched documents get their candidate
   bytes, every other document is read from the real tree unchanged (a cheap
   temporary directory of symlinks/hardlinks to untouched files plus the
   candidate bytes for touched ones, so large bundles are not fully copied);
3. run `check` against that candidate bundle — this is what makes cross-
   concept validation (broken links, uniqueness) see the mutation's full
   effect, not just the touched files in isolation;
4. if the candidate bundle is non-conformant, write nothing, leave the real
   bundle untouched, and exit non-zero with the diagnostics that would have
   been introduced;
5. only on success, replace each touched document's real bytes with its
   candidate bytes.

Step 5 is not a cross-file transaction — a crash between two file replacements
in a large batch can still leave a partial write — but by that point every
replacement is individually known-good, because validation already ran
against the full candidate state in step 3. That is a materially different
guarantee than validating after an unconditional write, which is what made
the earlier draft's "write, then report" approach reject-worthy: it could
publish a bundle `check` would fail before anyone ran `check` again.

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
- output payload shape mirrors `format`: `{"changed_paths", "skipped_paths", "succeeded", "written", "validation"}`.

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

### Mutate `bundle.concepts` in place without a flattened staging table

Rejected. `frontmatter_json` as a single JSON column forces every mutation
into `json_extract`/`json_set` expressions, which is exactly the SQL
ergonomics the issue explicitly wanted to avoid by choosing
`UPDATE ... SET field = ...` over a JSON-path DSL.

### Allow arbitrary DuckDB SQL, not just one parsed `UPDATE`

Rejected. Arbitrary SQL could target a table other than `__okf_staging`,
run multiple statements with independent side effects, or use DDL — none of
which the diff in step 2 or the target-column check in 1a/4 is built to
bound. Restricting `--sql` to one parsed `UPDATE` against the designated
staging table is what makes those two guarantees actual guarantees instead
of best-effort.

### Atomic replace across every touched file

Considered and deferred, but on a narrower question than before: staging and
validating the *whole candidate bundle* before any real write (decision 5)
already prevents the failure this RFC most needed to avoid — publishing a
bundle `check` would reject. What is still not solved is a crash **during**
the final replace step across many files, which could leave a batch of
already-validated writes partially applied. A write-to-temp-then-rename
protocol per file, plus a manifest of pending replacements resumable after a
crash, would close that gap; deferred because a partial-but-individually-
valid write, resumed by rerunning `apply`, is a materially smaller failure
mode than the invalid-bundle case decision 5 already closes.

## Open questions

- Exact type-inference rules for the flattened staging table: reuse
  `schema_contract.py`'s inference verbatim, or a narrower variant tuned for
  `UPDATE` ergonomics (e.g. always exposing every field as `string` so a caller
  never fights an inferred `int` column)?
- Cost of the candidate-bundle materialization in decision 5 on a bundle the
  size that motivated #26 (45,705 documents): symlinks/hardlinks for
  untouched files keep it cheap on a POSIX filesystem, but this needs
  measuring, not assuming, before the design is accepted.
- Whether TypeScript gets `apply` in the same release or follows later, given
  it has no native DuckDB dependency today outside `typescript-duckdb`.
