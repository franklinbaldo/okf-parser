---
type: RFC
title: Concept identity and rename semantics
status: accepted
description: Pin concept_id and logical_key as path-derived, non-authored identity for the filesystem provider, define a rename as an independent removal plus addition, and make digest equality an advisory signal a caller may compute — never one the parser infers on a concept's behalf.
---

# RFC 0023: Concept identity and rename semantics

## Summary

`concept_id` and `logical_key` are computed from a concept's bundle-relative
path, at load time, every time. Neither is authored, neither is persisted
independently of the path, and neither survives a move or rename: renaming
`notes/a.md` to `notes/b.md` is `notes/a` disappearing and `notes/b`
appearing, two unrelated identities as far as the parser is concerned.
`source_digest`/`parsed_digest` equality between the two is a fact a caller
can compute and treat as an advisory "this might be the same content that
moved" signal, but the parser never performs that inference itself and never
lets it override the path-derived id. This RFC writes down the identity
contract every later RFC that touches concept relations (RFC 0014's physical
materialization, RFC 0016's search index, RFC 0017's `fact` profile) already
assumes and quotes, and answers the specific questions
[#276](https://github.com/franklinbaldo/okf-parser/issues/276) raised about
it.

## Motivation

Path-based identity is simple, and simple is a feature: `concept_id` needs no
storage, no registry, and no coordination between writers, and two checkouts
of the same bundle at different absolute paths already agree on every
concept's id (`test_logical_keys_do_not_depend_on_checkout_path`). But
"simple" was never written down as a *decision* — it is only observable by
reading `parser.concept_id` and the tests that pin it. Meanwhile, later RFCs
cite RFC 0012 for this contract, including verbatim language about `diff` and
digest-based inference:

> A future source adapter may define another canonical identity under its
> own RFC. `diff` consumes the provider's canonical `concept_id`; it never
> guesses identity from title, body, or digest.
> — RFC 0012, quoted in RFC 0017

RFC 0012 (relational agent surfaces,
[#202](https://github.com/franklinbaldo/okf-parser/pull/202)) is still a
proposal, and identity is one section of a much larger read-service design.
This RFC extracts that section as a standalone decision, matching the
behavior RFC 0017 depends on and the test suite already enforces, so the
identity contract does not wait on the rest of RFC 0012. It also answers
#276's open questions directly rather than leaving them to be inferred from
call sites.

## Decision

### 1. `concept_id` is derived, never authored

`concept_id(bundle_root, path) = path.relative_to(bundle_root).with_suffix("").as_posix()`
(`src/okf_parser/parser.py`). A concept's frontmatter has no `id` field with
identity meaning to the core parser — a producer-defined `id:` key is
ordinary frontmatter data, not consulted for identity, exactly as
`docs/architecture.md` already says producer-defined strings "remain data,
even when they look like paths." There is no separate identity store; the id
is recomputed from the current directory listing on every `load_bundle`
call.

### 2. `logical_key` is a distinct field, currently equal to `concept_id`

`ConceptRecord` and every typed relation surface (Rust engine, DuckDB
extension, TypeScript client) carry `concept_id` and `logical_key` as two
columns, but `bundle.py` sets both from the same computed `doc_id` today.
The column stays distinct in the schema — not collapsed into one — because a
provider that defines its own canonical identity (decision 4) needs
somewhere to put an id that differs from the path-derived `concept_id`
without breaking every consumer that already selects a `logical_key` column.
For the shipped filesystem provider, treat the two as interchangeable; do
not depend on `logical_key` ever diverging from `concept_id` without a
provider that says otherwise.

### 3. A rename is removal plus addition, not a tracked move

The parser has no notion of "this concept used to be that concept." Moving
or renaming a file changes its bundle-relative path, which changes its
`concept_id`, which is indistinguishable — to `load_bundle`, to `concept()`,
to `resolve_relations()` — from deleting the old concept and creating an
unrelated new one at the new path. There is no rename event, no tombstone,
and no forwarding entry left behind at the old id.

This is deliberate, not an omission to fix later: a parser that tried to
recognize renames would have to guess identity from something other than
the path (title, digest, position in the file tree), and every such guess is
wrong for some legitimate edit — a rewritten concept moved at the same time
looks identical to an unrelated new concept replacing a deleted one at
digest granularity. Decision 5 below is the parser's answer: expose the
evidence, do not interpret it.

### 4. A future provider may define a different canonical identity, under its own RFC

Nothing here forbids a stable, authored identity that survives a move — RFC
0017's `fact` profile adds exactly that, scoped to directories that opt in
via a `.fact/` marker. What decision 3 forecloses is a *silent*
reinterpretation of plain OKF: for a markerless directory, the filesystem
provider's path-derived `concept_id` and its removed+added rename semantics
apply unconditionally. A provider that wants move-stable identity says so
explicitly, in its own RFC, and callers opt into that provider rather than
the parser quietly changing what "the same concept" means underneath an
existing bundle.

### 5. Digest equality is evidence a caller may use, never an inference the parser makes

`source_digest` and `parsed_digest` (`digests.py`) are deterministic
functions of a concept's exact source text and of its normalized
frontmatter+body, independent of path. A caller comparing two bundle
snapshots — before and after a rename, or between two branches — can compute
that a `removed` concept at one id and an `added` concept at another id
share a `parsed_digest`, and treat that as a *possible move* worth surfacing
to a human or an agent. The parser itself never performs this comparison,
never merges the two ids on the strength of it, and never lets a digest
match suppress the removed/added pair decision 3 already produced. Digest
equality is necessary but not sufficient evidence of continuity (a template
concept instantiated twice legitimately produces two different ids with
identical content) and its absence does not disprove continuity either (an
edit made in the same commit as a move changes the digest and the path
together). Treating it as advisory rather than authoritative keeps that
ambiguity visible instead of silently resolving it one way.

### 6. Old references are not migrated automatically

A frontmatter relation (`sources:`, `citations:`, any field `resolve_relations`
walks) that names a pre-rename path or id stops resolving the moment the
rename lands: `concept()`/`resolve_relations()` raise `KeyError` for an
unresolvable reference, by design — the same fail-loud posture the rest of
the core takes toward malformed relations (`docs/architecture.md`'s "strict
authored OKF parse"). Nothing walks the bundle rewriting inbound references
to follow a move. Repairing a stale reference after a move is authoring
work: a human or an agent edits the referencing frontmatter to the concept's
new id, the same way it would fix a reference that was wrong for any other
reason. A convenience command that moves a concept and rewrites the inbound
paths it can still resolve (RFC 0017 sketches `fact mv` as exactly this) is
a legitimate future addition, but it is tooling built on top of decisions
3–5, not a change to what identity means underneath it.

## Answers to #276, directly

- **Should stable IDs exist?** Not in the base filesystem provider — path
  *is* the id, by decision 1. A profile that wants authored, move-stable ids
  defines its own provider under its own RFC (decision 4); RFC 0017 is that
  RFC for the `fact` profile.
- **How are renames represented?** As an independent removal and addition
  (decision 3). There is no rename record in the base format.
- **How are old references migrated?** They are not migrated by the parser.
  A stale reference fails loudly (`KeyError`) until an author or a future
  move-aware command repairs it (decision 6).
- **How do agents recognize continuity?** By computing digest equality
  across a removed/added pair themselves and treating a match as advisory
  evidence, never as ground truth the parser already merged for them
  (decision 5).

## Non-goals

- A `diff`/`impact` command that classifies removed/added pairs as possible
  moves. This RFC pins what such a command may assume (decision 5); building
  it is separate, implementation work.
- Changing anything about the shipped filesystem provider's behavior. Every
  test in `tests/test_bundle.py` and `tests/test_concepts.py` that pins
  path-derived identity today continues to pass unchanged; this RFC adds a
  regression test for the rename case specifically (see
  `tests/test_concept_identity.py`).
- Retrofitting `.fact/`-style stable ids into plain OKF. That remains RFC
  0017's proposal, not this one's.
