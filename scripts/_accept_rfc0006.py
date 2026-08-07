from pathlib import Path
import re

RFC = Path("rfcs/0006-declared-column-types.md")
text = RFC.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    if old not in text:
        raise SystemExit(f"expected RFC text not found: {old[:120]!r}")
    text = text.replace(old, new, 1)


def replace_section(start: str, end: str, replacement: str) -> None:
    global text
    pattern = re.escape(start) + r".*?(?=" + re.escape(end) + r")"
    text2, count = re.subn(pattern, replacement.rstrip() + "\n\n", text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"expected exactly one section {start!r}, got {count}")
    text = text2


replace_once("status: proposed", "status: accepted")
replace_once(
    "description: Let an optional DuckDB DDL file sitting beside a type's specification document declare that type's physical column types and catalog comments, compiled into every relational surface, advisory in every direction and never a block",
    "description: Let an optional trusted DuckDB SQL file beside a type specification declare physical column types, compiled consistently into schema export, persistent DuckDB, and apply",
)
replace_once(
    "One\nbad document never widens the whole column back to `VARCHAR`. Divergence is\nadvisory by default (decisions 5 and 8).",
    "One\nbad document never widens the whole column back to `VARCHAR`. Value divergence\nis represented in data, not as a second control plane: the typed projection is\n`NULL` for that row and the raw carrier remains available (decisions 5 and 8).",
)
replace_once(
    "v1 reads column names, normalized logical types, and comments from the\ndeclaration catalog. Constraints are not read. `schema` exports the lossless\ntype IR; persistent `duckdb` materializes raw plus typed snapshot columns;\n`apply` is the remaining integration step and will use the same projection as\na generated read-only column (decision 7a). Nothing writes back into the\ndeclaration file (deferred to RFC 0007).",
    "v1 reads column names, normalized logical types, and comments from the\ndeclaration catalog. Constraints are not read. `schema` exports the lossless\ntype IR; persistent `duckdb` materializes raw plus stored typed snapshots; and\n`apply` materializes the same logical pair with a generated read-only typed\nprojection (decision 7a). Nothing writes back into the declaration file\n(deferred to RFC 0007).",
)

replace_section(
    "## Relationship to RFC 0005",
    "## Decision",
    """## Relationship to RFC 0005

Purely additive. A type with no declaration follows RFC 0005 unchanged. When a
valid declaration exists, its public fields gain typed projections while authored
source values remain available through compiler-owned raw carriers. A malformed
or ambiguous declaration is an invocation error on surfaces that were explicitly
asked to discover declarations; it is not silently reinterpreted as an absent
declaration. Value-level cast failure is different: it never rejects the bundle or
widens the column, because decision 5 represents it as typed `NULL` plus preserved
raw input.

This RFC adds no required bundle field and does not change runs that omit
`--spec-template`.""",
)

replace_once(
    "**Two types can still derive the same path, and this RFC diagnoses it\nrather than pretending otherwise.** `type_slug()` is deliberately not\ninjective — it strips accents and case — and RFC 0005 documents the exact\ncounterexample: `\"Revisao Ciencia\"` and `\"Revisão Ciência\"` both slug to\n`revisao-ciencia`. So two distinct types can point at one `.schema.sql`.\nThat is a **derived-path collision**: an advisory diagnostic naming both\ntypes and the shared file, and *neither* type gets a declaration — both\nfall back to RFC 0005 compilation (decision 7's whole-declaration\nfallback). Silently letting one type's declaration govern another's table\nwould be the one outcome worse than not typing either.",
    "**Two types can still derive the same path, and this RFC rejects the\nambiguity rather than guessing.** `type_slug()` is deliberately not injective —\nit strips accents and case — and RFC 0005 documents the exact counterexample:\n`\"Revisao Ciencia\"` and `\"Revisão Ciência\"` both slug to `revisao-ciencia`.\nWhen two types resolve to one declaration path, declaration discovery raises an\nexplicit error naming the shared path and owners. Neither type is allowed to\nborrow the other's declaration.",
)
replace_once(
    "**The template must reach every surface.** `--require-spec` is currently\n`check`'s alone (`cli.py`, `service.py`), so `apply`, `duckdb`, and\n`schema` have no way to derive anything today. This RFC adds the same\ntemplate option, under the name `--spec-template`, to all four commands and\nto their MCP equivalents; `check --require-spec` keeps its current name and\nmeaning (existence enforcement) and, when both are given, they must agree\nor it is an operator error. A command run without a template performs no\ndeclaration discovery at all — RFC 0005 behaviour throughout, no\ndiagnostics, which is what makes this opt-in at the invocation level too.",
    "**The template reaches the surfaces that actually consume declarations.**\n`schema`, `duckdb`, and `apply` accept `--spec-template`; `init` uses the same\ntemplate to scaffold narrative specs and optionally starter declarations.\n`check --require-spec` remains a separate existence rule for specification\ndocuments and does not execute `.schema.sql`. A relational command run without\n`--spec-template` performs no declaration discovery at all, which preserves RFC\n0005 behaviour and keeps execution of trusted SQL explicitly opt-in.",
)

replace_once(
    "A script that fails to execute, or whose catalog afterward holds zero,\ntwo, or a differently-named table for the type, is a **malformed\ndeclaration**: advisory diagnostic, and that type compiles exactly as if\nno declaration existed (decision 7). Everything else about *how* the\ntable came to exist — one `CREATE TABLE`, a CTAS over a `read_csv`, a\nchain of temp tables joined together — is invisible past that check.",
    "A script that fails to execute, or whose catalog afterward does not expose\nthe required table identity, is a **malformed declaration** and the consuming\ncommand fails explicitly. The caller opted into executing a declaration; silently\ntreating a broken file as if it were absent would hide authoring mistakes and make\nthe same bundle compile differently depending on whether anyone noticed an\nadvisory message. Everything else about *how* the table came to exist — one\n`CREATE TABLE`, a CTAS over a `read_csv`, or staging tables joined together — is\ninvisible past the catalog post-condition.",
)

replace_section(
    "### 5b. Declared, undeclared, and unobserved columns",
    "### 6. Comments:",
    """### 5b. Declared, undeclared, and unobserved columns

A declaration and the observed documents need not agree on the column set:

- **declared and observed** — typed per decision 5;
- **declared, never observed** — the public column still exists with its declared
  DuckDB type and is `NULL` for every row; declaration is producer intent, not a
  summary of only today's populated keys;
- **observed, not declared** — scalar fields remain ordinary `VARCHAR` columns, as
  RFC 0005 already compiles them. A declaration is not a closed-world schema and
  never suppresses authored data. Structured undeclared values remain outside
  `apply`'s writable scalar namespace under RFC 0005's existing rule.

Two names that collide under DuckDB's case-insensitive identifier equality are one
column; the declared spelling is canonical when one side is declared. Two declared
columns that collide are rejected by DuckDB while executing the declaration.

Compiler-owned `__okf_` names are outside the public declared field set. A
`.schema.sql` entry under that prefix is ignored when the public typed plan is
built; those names remain owned by the compiler.""",
)

replace_section(
    "### 6. Comments:",
    "### 7. Divergence",
    """### 6. Comments: persistent catalog metadata follows the declaration

`COMMENT ON TABLE` and `COMMENT ON COLUMN` are read from the declaration catalog.
Persistent `duckdb` re-applies declared comments to `{schema}_types` so SQL clients
see the authored documentation next to the typed snapshot. On explicit overwrite,
an existing comment for which the declaration supplies no replacement is preserved
rather than silently destroyed.

`apply` does not materialize comments into its ephemeral catalog: comments do not
affect filtering, mutation, or writeback, and that database disappears at the end
of the invocation. `schema` currently consumes declared physical types, not catalog
comments, so this RFC does not claim a description mapping that the exporters do
not implement.

Comments do not enter `apply`'s bounded `--sql` grammar. Declaration prose is edited
in `.schema.sql`; Git remains the history mechanism. Declaration/comment writeback
is deferred to RFC 0007.""",
)

replace_section(
    "### 7. Divergence",
    "### 7a. `apply`:",
    """### 7. Declaration failure and value divergence are different contracts

There is no advisory-fallback state machine in v1.

- **No declaration file** is normal opt-out: RFC 0005 behaviour applies.
- **A declaration was requested but cannot be discovered, read, executed, or matched
  to its concept type** is an explicit command error. Derived-path collisions are
  errors for the same reason: there is no unambiguous declaration to compile.
- **A valid declaration meets a value that DuckDB cannot cast** is data, not a
  declaration failure. `TRY_CAST` yields typed `NULL` for that row while the raw
  carrier remains unchanged; the public column keeps its declared type.
- **An export target has a smaller vocabulary than DuckDB** is target-local. The
  lossless DuckDB type stays in the IR/metadata and the target emits only semantics
  it can state truthfully.

This separation deliberately avoids a second diagnostics protocol in the core type
contract. Strict domain validation, quality reports, or richer drift diagnostics can
be layered on top of raw-versus-typed data later without changing what a declaration
means.""",
)

replace_section(
    "### 7a. `apply`:",
    "### 7b. `duckdb`:",
    """### 7a. `apply`: typed columns are generated and read-only

`apply --spec-template` materializes declared fields as a compiler-owned raw carrier
plus a DuckDB `GENERATED ... VIRTUAL` public projection using `TRY_CAST`. This makes
real typed predicates available inside the existing RFC 0005 SQL surface while
leaving authored YAML text as the writeback source of truth.

DuckDB itself rejects direct `UPDATE` assignment to a generated public field. The
raw carrier is an ordinary physical column, so `apply` extends its dynamic protected
column set with every `__okf_raw_<field>` and rejects attempts to mutate or reshape
those compiler-owned columns.

Schema operations follow the public field contract:

- renaming a declared public field is rejected; changing its name belongs in the
  declaration;
- re-adding a name still owned by the declaration is rejected;
- dropping a declared public field is allowed as a logical field deletion for that
  invocation. The generated public column disappears, the internal raw carrier
  remains protected, and writeback removes the authored frontmatter key. The
  declaration file itself is unchanged, so a later run will still know that field's
  declared type if it reappears;
- ordinary RFC 0005 ADD/DROP/RENAME operations on undeclared scalar fields remain
  available. Verified against DuckDB 1.5.5, `ALTER ... ADD COLUMN` works on a table
  that also contains virtual generated columns.

Generated declared values are never serialized back to YAML. Making typed fields
writable requires canonical per-type serialization and remains RFC 0007 work.""",
)

replace_once(
    "**Raw columns are part of the persisted table, queryable, and not treated\nas a second thing to hide or export.** `{schema}_types` is not designed\nhere as a stripped-down public view — it is the same table shape decision\n5 already builds for `apply`'s ephemeral database, persisted. Each\ndeclared field's hidden `__okf_raw_<field>` column sits beside its\ngenerated column in the physical table, appears in `SELECT *`, and is\navailable for a consumer who wants to audit \"what did the compiler\nactually see\" against \"what the typed projection computed\" — the same\ncomparison decision 5's design exists to make possible. The `__okf_`\nprefix is the signal that it is compiler-owned and outside the declared\nsurface, exactly as it already signals for `__okf_path`/`__okf_body` on\nthe four contract tables; nothing new is being asked of that prefix here.",
    "**Raw columns are part of the persisted table and remain queryable.** In\n`{schema}_types`, each `__okf_raw_<field>` carrier sits beside an ordinary stored\ntyped snapshot. This is deliberately not the same physical table shape as\n`apply`: both consumers share the logical raw-plus-typed plan, but persistent\n`duckdb` stores the typed result once while ephemeral `apply` uses a virtual\ngenerated projection. The `__okf_` prefix marks the raw carrier as compiler-owned\nin both cases.",
)

replace_section(
    "### 8. `--fail-on-spec-divergence`",
    "### 9. `schema`",
    """### 8. No divergence-specific escalation flag in v1

The earlier proposal for `--fail-on-spec-divergence` is rejected from this RFC.
Malformed declarations already fail explicitly, so an escalation flag adds nothing
there. Value divergence is represented directly by `raw != NULL` alongside a typed
`NULL` (or other normalized typed result) and does not change the schema contract.

A future validation/reporting RFC may define domain-specific drift diagnostics or a
strict quality gate, but RFC 0006 does not invent a second exit-code protocol merely
to restate information already present in the relational representation.""",
)

replace_once(
    "Divergence diagnostics remain advisory and do not alter the emitted schema. The\ndiagnostics transport described by decision 8 can be implemented independently of\nthis contract decision; until then, declaration parsing failures are surfaced\nexplicitly and value drift does not change output shape.",
    "Value divergence does not alter the emitted schema. Declaration discovery or\nexecution failure is surfaced explicitly; successfully declared type intent remains\nstable regardless of the current row values.",
)

replace_once(
    "  `SELECT` tests every column's every candidate together: a single\n  vectorized DuckDB round trip, not a query per field per candidate.\n  A field observed as a list or map on even one document is dropped\n  entirely, the same shape restriction decision 5a already places on\n  declared columns.",
    "  `SELECT` tests every column's every candidate together: a single\n  vectorized DuckDB round trip, not a query per field per candidate. This is\n  a starter-schema proposal heuristic, not decision 5's runtime semantics:\n  runtime declarations always use DuckDB `TRY_CAST` per value. A field observed\n  as a list or map on even one document is omitted from this starter inference.\n",
)
replace_once(
    "Decision 1 already diagnoses two types slugging to the same document as an\nadvisory violation `check` reports but neither the type keeps its\ndeclaration.",
    "Decision 1 already treats two types slugging to the same declaration path as\nan ambiguity that declaration discovery refuses to compile.",
)

for stale in (
    "--fail-on-spec-divergence",
    "remaining integration step",
    "MCP equivalents",
    "whole-declaration fallback",
    "advisory by default",
):
    if stale in text:
        raise SystemExit(f"stale RFC contract remains after reconciliation: {stale}")

RFC.write_text(text, encoding="utf-8")

# Patch release versions. Locks are regenerated by the temporary workflow.
for path in (
    Path("pyproject.toml"),
    Path("typescript/package.json"),
    Path("typescript/src/version.ts"),
    Path("typescript-duckdb/package.json"),
):
    value = path.read_text(encoding="utf-8")
    if "0.18.0" not in value:
        raise SystemExit(f"0.18.0 not found in {path}")
    path.write_text(value.replace("0.18.0", "0.18.1"), encoding="utf-8")

changelog = Path("changelog/0.18.1.md")
changelog.write_text(
    """---
type: Release
title: okf-parser 0.18.1
description: accept RFC 0006 after reconciling its normative text with the implemented type pipeline
---

# okf-parser 0.18.1

## RFC 0006 accepted

RFC 0006 now documents the behavior shipped across `schema`, persistent `duckdb`,
and `apply` through 0.18.0 and moves from `proposed` to `accepted`.

The reconciliation removes superseded proposal text rather than adding runtime
behavior:

- malformed or ambiguous declarations are explicit command errors when declaration
  discovery was requested; absence of a declaration remains normal opt-out;
- value divergence stays row-local through DuckDB `TRY_CAST`, with raw source
  preserved and no whole-column fallback;
- the never-implemented `--fail-on-spec-divergence` diagnostics protocol is removed
  from the RFC;
- persistent `duckdb` owns catalog comments; ephemeral `apply` does not pretend to
  persist comment metadata;
- `apply`'s generated typed columns, protected raw carriers, logical DROP semantics,
  and compatibility with ordinary RFC 0005 ALTER operations are documented as
  implemented and tested against DuckDB 1.5.5;
- `init --infer-schema` is explicitly a proposal heuristic, not the runtime cast
  semantics of declared fields.

No parser/runtime behavior changes in this release.
""",
    encoding="utf-8",
)
