---
type: RFC
title: gitignore semantics for .okfignore
status: accepted
description: Adopt gitignore pattern semantics, including negation, so a mixed repository can express what it actually means
---

# RFC 0004: `.gitignore` semantics for `.okfignore`

## Summary

`.okfignore` adopts `.gitignore` pattern semantics in both runtimes: unanchored
patterns match at any depth, a separator anchors at the bundle root, a trailing
`/` matches directories only, `!` re-includes, and the last matching pattern
decides a path's fate. One deviation is kept and documented: a negation works
even when a parent directory is excluded.

## Motivation

The original semantics were deliberately narrower than `.gitignore`: every
pattern anchored at the root, no negation, and leading or trailing separators
dropped. The reasoning was that a pattern which silently widened its own scope
would drop documents the author meant to validate.

Two facts turned that caution into a defect.

**The exclusion an author needs is inexpressible.** A monorepo that vendors
dependencies and keeps knowledge inside them cannot say "exclude `vendor`
except `vendor/knowledge`" at all. Without negation the only alternatives are
enumerating every excluded sibling by hand, or moving the knowledge — a layout
change forced by a limitation of the ignore file.

**The filename promises semantics the file does not honour.** A file named like
`.gitignore`, documented with a `gitignore` code fence, that matches by
different rules is a trap. The evidence was in the README: an entire paragraph
existed to explain which half of the reader's habit applied. Documentation that
long about a difference is a signal that the difference is the bug.

Reported in #24, where a repository with 45,705 tracked Markdown files could
not be validated from its real root without hand-writing the rules that the
`.gitignore` beside it already expressed.

## Decision

Match `.gitignore`:

- a pattern with no separator matches its name at any depth;
- a separator at the start or middle anchors the pattern at the bundle root;
- a trailing `/` matches directories only;
- `*` and `?` stay inside one segment; `[abc]` and `[!abc]` classes are
  supported; `**` spans segments;
- `!` negates, and the **last** matching pattern decides;
- `#` starts a comment, blank lines declare nothing, unescaped trailing spaces
  are dropped, and `\#` / `\!` escape a literal first character;
- a path with no rule of its own inherits the decision of the nearest directory
  above it.

### The one deviation

Git cannot re-include a path whose parent directory is excluded, because it
prunes the walk and never reconsiders: `vendor` followed by `!vendor/knowledge`
re-includes nothing there. This implementation re-includes it.

A negation that silently does nothing is precisely the class of surprise the
original design set out to avoid, and reproducing git's limitation would
require an author to know that `vendor/*` and `vendor` differ in a way nothing
in the file suggests.

The cost is walking a directory that a rule excluded. It is bounded: discovery
prunes as before whenever the rules contain no negation, so the large vendored
tree that motivated pruning is unaffected unless the author asked for a
re-inclusion inside it.

## Cross-runtime contract

The matching rules are one observable contract, so both runtimes are driven by
`conformance/exclusion.json`: a list of pattern sets, each with the paths it
must and must not exclude, including the directory-only cases. Python and
TypeScript run the same fixture.

An off-the-shelf library on each side — `pathspec` and `ignore` — was rejected
for that reason: two independent implementations agreeing only by luck, with no
way to fix a divergence found in a dependency.

## Compatibility

This changes what existing files mean, so it ships as a minor version with a
migration table in the README:

| before   | after     | why                                               |
| -------- | --------- | ------------------------------------------------- |
| `*.md`   | `/*.md`   | unanchored patterns now match at any depth         |
| `vendor` | `/vendor` | keep it root-only; leave as-is to match any depth  |

A pattern beginning with a literal `!` now needs `\!`.

The change is detectable rather than silent in the direction that matters: the
new semantics exclude *more*, so a stale pattern reports fewer documents rather
than validating a tree the author believed was filtered. `check` reports the
Markdown count, which is where an over-broad pattern shows up.

## Alternatives considered

### Keep the narrow semantics and rename the file

Renaming to something that does not evoke `.gitignore` would remove the trap
without adding negation. Rejected: the missing capability is real, and the
familiar name is worth keeping precisely because the habit is widespread.

### Read `.gitignore` itself

Rejected separately from this RFC. It couples validation to VCS state — a
legitimate bundle inside an ignored path would silently vanish from `check` —
and faithful support means reproducing git's per-directory precedence,
`.git/info/exclude` and the user's global file. If it is ever added it should
be an explicit `--respect-gitignore`, not a default.

### Reproduce git's re-inclusion limitation exactly

Rejected. See "The one deviation".
