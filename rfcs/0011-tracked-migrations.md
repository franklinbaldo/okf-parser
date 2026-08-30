---
type: RFC
title: Tracked bundle migrations
status: proposed
description: Add an idempotent, chain-tracked migrate command that runs a schema-evolving apply script at most once per bundle, per concept type, ordered the way Alembic orders revisions — verified by content hash and an explicit depends_on link rather than by inferring re-application from the ALTER's catalog side effect.
---

# RFC 0011: Tracked bundle migrations

## Summary

Add `okf-parser migrate <bundle> <id> --for-type "<type>" --depends-on
<id|(none)> --sql "..."` — the same leading-ALTER + trailing-UPDATE
contract `apply` already accepts (RFC 0005), but recorded in a persistent,
bundle-local, **per-type chain**, the same shape as Alembic's
`revision`/`down_revision`: unchanged `sql` for a known `id` is a no-op
success; changed `sql` for a known `id` is a rejected integrity error;
`--depends-on` naming anything other than that type's current chain tip
(including `(none)` when the type already has one) is a rejected ordering
error, attempted before the script ever runs; and an unknown `id` whose
`--depends-on` matches the tip runs the script exactly as `apply` would
and then extends the chain. The ledger entry is itself an ordinary OKF
concept (`type: Migration`), so it is readable, diffable, and greppable
the same way as everything else in a bundle — no side-channel state file,
no reserved binary format, and the type's migration history is the chain
itself, not a flag on an otherwise-unordered set of ids.

## Motivation

[#152](https://github.com/franklinbaldo/okf-parser/issues/152) measured
that `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` does not make a caller's
own `apply` script safely re-runnable, and the reason is structural, not a
missing DuckDB feature:

```
$ okf-parser apply <bundle> --sql "ALTER TABLE \"Tipo\" ADD COLUMN IF NOT EXISTS campo VARCHAR; UPDATE ..."
# campo already exists on some document:
{"error": "ALTER statement must affect exactly one type's table: ...", "succeeded": false}
# campo does not exist yet:
{"succeeded": true, "written": true, ...}
```

`apply` (`_check_alter_shape` in `apply.py`) infers what an `ALTER` did
*from the catalog's own before/after diff* — deliberately, per RFC 0005,
so the compiler never has to parse or trust what the script's SQL text
claims to do. `IF NOT EXISTS` makes the second run's `ALTER` a true no-op:
the catalog does not change, so there is no diff to infer from, and the
whole script is rejected as touching zero (rather than exactly one)
type's table. An idempotent operation has no side effect on its second
run by definition — asking `apply` to recognize "already done" from a
side effect it engineered away is unsound, not just presently unimplemented.

The same problem recurs for every schema evolution that must be safe to
re-run: renaming a field, splitting one field into two, changing a
declared type (RFC 0006). Today every caller — including the script in
[pge-iperon/judicial](https://github.com/pge-iperon/judicial) that
motivated #151/#152 — has to read the whole bundle first, itself, only to
decide whether to send the `ALTER` at all. That bookkeeping belongs to the
parser, once, not to every consumer.

A flat set of `id`s checked only by hash (an earlier draft of this RFC)
still leaves a second failure mode open: nothing stops two callers, or
one caller run twice against diverged bundle copies, from applying the
*same set* of migrations in a *different order* — silently reordered
`ADD COLUMN`s land the same final schema, but a rename followed by a
retype is not commutative with the reverse order in general. Alembic
closes this with `down_revision`: every revision names exactly the one
revision it was authored on top of, so the tool can refuse to apply a
revision whose declared parent is not the current tip, and "what has run,
in what order" is a linked list, not a set. `migrate` adopts the same
shape, scoped per concept type (`apply`'s own single-type-per-script
invariant already makes "type" the natural unit a chain belongs to —
there is no single global schema version to be tip of).

## Non-goals

- A general workflow/DAG engine, branching, or a `migrate up`/`down`
  pair. Per type, the chain is linear — one tip, one next migration — the
  same restriction Alembic places on a single branch. `migrate` runs
  exactly the one migration named by `id`; a caller sequences its own
  calls, the same way it already sequences its own `apply` calls.
- Retroactively making arbitrary `apply` scripts idempotent. `apply`'s
  contract (RFC 0005) does not change. `migrate` is a second entry point
  built from the same primitives, not a mode switch on `apply`.
- Cross-bundle migration state. The chain lives inside the bundle it
  migrates; there is no registry of "migrations run across every bundle
  I've ever touched."

## The ledger is an ordinary concept, not a side-channel

A migration record is a normal OKF document:

```markdown
---
type: Migration
id: add-campo-novo
for_type: Tipo
depends_on: rename-campo-antigo   # previous migration id for *this* for_type, or omitted for the first
sql_sha256: 3f9a2b...             # sha256 of the exact --sql text, hex-encoded
ran_at: 2026-08-17T19:04:00Z
---
```

stored at a fixed, conventional path derived from `id`:
`migrations/<id>.md` (the id, not a slug of the SQL, so the filename is
stable across content-preserving edits to the SQL's whitespace — which
`sql_sha256` still catches as a content change). `id` is global across the
bundle (a migration touching one type must still not collide in name with
one touching another), but `depends_on` only ever names another migration
sharing the same `for_type` — the chain-tip lookup below filters by
`for_type` before it looks at `depends_on` at all.

This is a deliberate choice over a reserved binary/JSON state file or a
DuckDB table living only in the ephemeral in-memory catalog `apply`
already tears down after every run:

1. **No new persistence mechanism.** `okf-parser` already knows how to
   read, validate, and write OKF concepts. A `Migration` concept needs
   zero new file-format code — `parse_document`, `load_bundle`, and the
   existing frontmatter writer all already handle it.
2. **Visible and diffable.** `git log migrations/add-campo-novo.md` is the
   audit trail for free. A JSON ledger file would need its own diff-review
   convention; a `Migration` concept gets one for free, same as every
   other concept type.
3. **Queryable the same way as everything else.** Once
   [#151](https://github.com/franklinbaldo/okf-parser/issues/151)'s
   read-only `query` command exists, `SELECT id, ran_at FROM "Migration"`
   answers "what has run against this bundle" with no new API.
4. **No reserved-path ambiguity.** `migrations/` is an ordinary directory;
   concept scanning does not need a new "this path is special" rule the
   way `okf.schema.sql` (RFC 0007) or `index.md`/`log.md` needed one.
   (A bundle is free to declare a `Migration` spec via RFC 0006 later, the
   same as any other type, if it wants declared field types on this
   concept too — v1 does not require it.)

## Command contract

```
okf-parser migrate <bundle> <id> --for-type "<type>" \
  --depends-on <previous-id-or-omit-for-the-first> \
  --sql "<leading ALTERs>; <trailing UPDATE>" [--write]
```

Same `--sql` grammar `apply` accepts: zero or more leading
`ALTER TABLE ... ADD/DROP/RENAME COLUMN` statements, then exactly one
trailing `UPDATE` against `--for-type`'s table (the pre-existing "script
touched more than one type" check already rejects a script whose `ALTER`/
`UPDATE` names a different table). Same `--write`/dry-run split as `apply`
and `import`: without `--write`, nothing is touched, the result reports
what *would* happen — including which chain-order outcome it would be.

### Decision, before any bundle mutation is attempted

1. Load the bundle's `Migration` concepts (a normal `load_bundle` +
   `concept_type == "Migration"` filter — no new bundle-loading code) and
   index them by `id`, plus the subset with `for_type == --for-type`.
2. **A concept with this `id` already exists.** Its `for_type` and
   `depends_on` must equal what was just given (an `id` names one
   operation permanently, not just one SQL text) — a mismatch on either
   is an integrity error, same shape as a `sql_sha256` mismatch below.
   Then:
   - `sql_sha256` equals `sha256(sql)` → **no-op success**.
     `{"succeeded": true, "written": false, "already_applied": true,
     "ran_at": "<recorded timestamp>"}`. Nothing else about the bundle is
     read — deliberately cheaper than materializing `--for-type`'s table
     only to find there's nothing to do.
   - `sql_sha256` differs → **integrity error**, no bundle mutation
     attempted: `{"succeeded": false, "error": "migration \"<id>\"
     already ran with different SQL (recorded sha256 <a>, given <b>)"}`.
3. **No concept with this `id` exists.** Compute `--for-type`'s current
   chain tip: the one `Migration` concept with that `for_type` that no
   other such concept's `depends_on` names (the chain has exactly one row
   with no incoming edge, or the type has no migrations yet — anything
   else, a fork or a cycle, is a pre-existing corruption this step
   reports rather than silently resolves). Then:
   - `--depends-on` names that tip (or the type has no migrations yet and
     `--depends-on` was omitted) → **run** (see below), then extend the
     chain.
   - Anything else — names a migration that is not the tip, names one
     under a different `for_type`, omits `--depends-on` when a tip
     already exists, or names one when the type has none yet — is a
     **rejected ordering error**, no bundle mutation attempted:
     `{"succeeded": false, "error": "migration \"<id>\" declares
     depends_on <given>, but the current tip for type \"<for_type>\" is
     <tip-or-none>"}`. This is the check that makes reordering or
     skipping a migration fail loudly instead of landing an
     order-dependent schema silently.

### Run, when the id is unknown

Steps 2–5 of `apply_bundle` (RFC 0005) unchanged: snapshot, validate,
materialize declared/undeclared type tables, run the script transactionally
in the ephemeral DuckDB catalog, compile the row diff purely from the
final relational state. `migrate` reuses `apply.py`'s private
`_materialize`/`_execute_script`/`_compile_row_diff` machinery directly —
it is the same operation, not a reimplementation.

The only new step: the same `--write` (or preview-token-reviewed) call
that stages the touched concepts' candidate files **also** stages one new
file, the `Migration` ledger concept, and the write-time freshness/
conflict check (`stage_validate_write` in `write_support.py`) covers both
kinds of change in the same pass. If the script materializes zero row
diffs (a legitimate outcome — e.g. an `ADD COLUMN` with no rows to
`UPDATE` yet), the ledger entry is still written: the migration *ran*,
even if it happened to change nothing, and must not run again.

## Required change to `write_support.py`: candidate trees may add files

`build_candidate_tree` (used by `apply`'s `stage_validate_write`) only
*substitutes* files it finds while walking the real bundle — it has no
path for "this file does not exist in `root` yet, write it into the
candidate tree anyway." `import_bundle`'s new-file writes
(`bundle_import.py`) go around this machinery entirely: a direct
`destination.write_text`, with none of `apply`'s coherent-snapshot,
candidate-tree-validation, or write-time-conflict checks.

`migrate` needs both kinds of change validated and written together,
atomically, in one `stage_validate_write` call — a partial write (script
ran, ledger not recorded, or vice versa) is exactly the split-brain state
this RFC exists to prevent. This RFC proposes extending
`build_candidate_tree`/`stage_validate_write` with a second, additive
candidate map (new relative path → raw bytes to create) alongside the
existing substitution map, so a path that does not yet exist in `root` is
written into the candidate tree instead of being skipped. The rest of the
pipeline (`validate_path` on the candidate tree, the manifest-based
freshness recheck, the final atomic replace) already generalizes to an
added file without further changes — `stage_validate_write`'s freshness
check already treats `baseline_keys_set ^ current_keys_set` (a path that
appeared or disappeared since the snapshot) as a conflict, which is
exactly right for a new ledger file too: if something else created
`migrations/<id>.md` between snapshot and write, that's a real race to
reject, not silently overwrite.

## What `migrate` deliberately does not solve

- **Concurrent migrations racing for the same chain tip.** Two `migrate`
  invocations both computing `--for-type`'s tip before either has written
  can both believe they extend it. The existing preview-token /
  manifest-freshness conflict check (RFC 0005) still catches this — the
  loser's write-time recheck sees the winner's new `migrations/<id>.md`
  that was not in its snapshot and reports a conflict, the same as two
  concurrent `apply` calls racing on any other file. No new locking
  primitive; the tip-mismatch check above handles *authoring* order, this
  existing mechanism handles *racing* order.
- **Rollback.** A migration's `UPDATE`/`ALTER` is forward-only, same as
  `apply`. Undoing one is authoring and running a new migration with a
  new `id`, the ordinary way schema history works in this model.

## Open question for the author

Should `Migration` concepts default to being counted by
`okf-parser inventory`/`okf-parser duckdb` alongside every other type
(the "no reserved-path ambiguity" property above), or excluded from
`okf-parser check`'s normative surface the way `index.md`/`log.md` are?
This RFC assumes the former (an ordinary concept, no special-casing) —
flagging it explicitly since it's the one place this design differs from
the other reserved-file precedents (RFC 0007's `okf.schema.sql`,
`index.md`/`log.md`) and is worth a deliberate yes rather than a default.
