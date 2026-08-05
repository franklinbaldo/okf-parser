---
type: RFC
title: Relational writes to frontmatter fields
status: proposed
description: Define an `apply` command that mutates frontmatter fields through SQL against the materialized concept relation, with round-trip fidelity, dry-run and post-write validation
---

# RFC 0005: Relational writes to frontmatter fields

## Summary

Add `okf-parser apply PATH --sql "UPDATE concepts SET ... WHERE ..."`, a
command that mutates frontmatter fields in place across a bundle. It reuses
the existing relational compilation (`duckdb`, `schema`) to select and
express the mutation, but writes back through the same round-trip-preserving
Markdown/YAML path `format` already depends on — not by re-serializing
frontmatter wholesale.

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
column holding everything else as a JSON string — there is no `setor` column
to `SET`. The issue's SQL examples imply per-field columns; the materialized
schema does not have them, and giving every observed frontmatter key its own
column is exactly the type inference `schema_contract.py` already does for
`okf-parser schema`, reapplied to a different output.

So `apply` cannot run the caller's SQL against `bundle.concepts` as-is. It
must compile a wider, ephemeral view first.

## Decision

### 1. Compile a flattened view per invocation

Before running the caller's SQL, `apply` builds a temporary DuckDB view over
`bundle.concepts` that unnests `frontmatter_json` into one column per key
observed anywhere in the selected concepts — the same key/type discovery
`schema_contract.py` performs for `schema --infer-types`, run here to shape
columns instead of a JSON Schema. Promoted columns (`concept_type`, `title`,
`description`) and `path` (read-only, see below) are included alongside.

The caller's `UPDATE concepts SET ... WHERE ...` runs against this view, not
the raw table. This is what makes the issue's examples work as written:

```bash
okf-parser apply ./bundle --sql "
  UPDATE concepts
     SET setor = '#GAB#FSB'
   WHERE setor IS NULL AND path LIKE 'rotinas/%'
" --write
```

A simpler surface without `WHERE` is also offered for the common case, so a
caller who only needs unconditional rename does not need to write SQL:

```bash
okf-parser apply ./bundle --field type --from "Revisao Ciencia" --to "Revisão Ciência"
```

`--field/--from/--to` is sugar for a one-column `UPDATE ... WHERE field = from`; it exists because the SQL form makes the trivial case verbose, not
because selection needs a second implementation.

### 2. Diff before write, one document at a time

After the SQL runs, `apply` diffs the view's post-mutation row against its
pre-mutation row per `concept_id`. Only documents with at least one changed
column are touched. The diff, not the SQL statement, is what a `--write`-less
run reports — this keeps the dry-run output meaningful even for SQL with
side effects that end up being no-ops (idempotency, below).

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
- preserve the file's original BOM and line-ending style, the same contract
  `format` already honors for the body.

If the round-trip loader cannot parse a document's frontmatter losslessly
(an anchor, a merge key, a construct `ruamel.yaml` cannot round-trip), `apply`
skips that document and reports it — mirroring `format`'s `skipped_paths` for
documents whose protected block structure a rewrite would change — rather
than falling back to a lossy rewrite.

### 4. Refuse non-writable columns

`concept_id`, `logical_key`, and `path` are derived from the filesystem, not
authored. `SET path = ...` is a rename, a different and unimplemented
operation; `apply` rejects any statement whose target list includes them,
before running anything, with an error naming the column.

### 5. Validate after writing

`apply --write` runs `check` against every document it touched once the
writes land, and reports (but does not roll back) any new nonconformance —
the same posture `duckdb`'s transaction takes internally, kept honest here
because frontmatter writes are filesystem mutations with no transaction to
roll back across multiple files. A mutation that turns a conformant bundle
non-conformant is not silently accepted; it is surfaced in the same payload,
under a `validation` key, so CI can fail on it.

### 6. Idempotency

Running the same `apply` invocation twice must produce no diff on the second
run. This falls out of steps 2 and 3 by construction — a `WHERE` clause that
already holds is a no-op diff — but the conformance corpus (`conformance/*`)
gets fixtures that assert it explicitly, since it is the property the "run
this migration in CI to keep it converged" use case depends on.

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

### Mutate `bundle.concepts` in place without a flattened view

Rejected. `frontmatter_json` as a single JSON column forces every mutation
into `json_extract`/`json_set` expressions, which is exactly the SQL
ergonomics the issue explicitly wanted to avoid by choosing `UPDATE ... SET field = ...` over a JSON-path DSL.

### Transactional multi-file write

Considered and deferred. A true transaction across N Markdown files (all
writes succeed or none do) would need a staging-then-atomic-rename protocol;
worth revisiting if `apply` sees adoption on bundles where a partial write is
actively harmful, but the issue's stated use cases (bulk rename, backfill,
normalization) tolerate a partial write followed by a second idempotent run.

## Open questions

- Exact type-inference rules for the flattened view: reuse
  `schema_contract.py`'s inference verbatim, or a narrower variant tuned for
  `UPDATE` ergonomics (e.g. always exposing every field as `string` so a caller
  never fights an inferred `int` column)?
- Whether `--sql` should be restricted to a single `UPDATE` statement (safer,
  simpler to diff) or allow arbitrary DuckDB SQL against the view (more
  powerful, harder to bound what "changed" means for the diff in step 2).
- Whether TypeScript gets `apply` in the same release or follows later, given
  it has no native DuckDB dependency today outside `typescript-duckdb`.
