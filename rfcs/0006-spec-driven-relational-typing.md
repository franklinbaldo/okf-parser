---
type: RFC
title: Spec-driven relational typing and bidirectional spec sync
status: proposed
description: Let an optional sibling JSON Schema file declare a type's schema and documentation, compiled into DuckDB types and COMMENT ON metadata, with ALTER TABLE writing back to the schema that declared it
---

# RFC 0006: Spec-driven relational typing and bidirectional spec sync

## Summary

RFC 0005 compiles each concept type into a DuckDB table by inferring every
column as `VARCHAR`, with no persisted schema and no documentation beyond
what a caller happens to know. This RFC adds an **optional** second
compilation input: if a type has a specification document, `apply` compiles
its declared field types and descriptions into the table — real DuckDB
column types where the data actually supports them, and `COMMENT ON
TABLE`/`COMMENT ON COLUMN` metadata carrying the specification's own prose —
instead of leaving inference and documentation as something a caller
re-derives every time.

The specification is a pair of sibling files, not a single document:

```text
rotina.md            # narrative documentation; frontmatter references the schema
rotina.schema.json    # JSON Schema Draft 2020-12; the structural contract
```

`rotina.md` is the human-facing document `--require-spec` already looks for
— its frontmatter carries only a relative reference to the schema file. The
schema file is the canonical contract: **JSON Schema Draft 2020-12**, not a
format this RFC invents. A deliberately small v1 profile of JSON Schema is
interpreted by the compiler (decision 5); everything outside that profile is
preserved and ignored, never rejected — decision 5 states exactly what
"preserved" guarantees and does not.

The other direction is what makes this more than a read-only convenience:
when a schema exists, an `ALTER TABLE` in RFC 0005's bounded script (`ADD
COLUMN`, `DROP COLUMN`, `RENAME COLUMN`) writes back into the schema file
that declared the type, not only into the ephemeral in-memory table.
Without that, a schema change would be silently undone the next time
`apply` runs, because the schema would recompile the old shape.

Nothing here is required. A type with no specification compiles exactly as
RFC 0005 already defines — every column `VARCHAR`, no comments, no casting
— unchanged. This RFC is additive, not a new obligation on bundles that
don't opt in.

## Motivation

`okf-parser check --require-spec` (0.14.0) already established that a
type's specification document is worth having and worth deriving a path
for. It stops at *existence*: the rule is "does a document exist at this
path," not "does this document's own claims about the type's fields hold."
That gap is what this RFC closes:

1. **Types and comments as durable metadata, not throwaway inference.**
   RFC 0005's `VARCHAR`-only tables are correct for safety but throw away
   information a bundle author already has: this field is really a
   `datetime`, this table means *this*, this column means *that*. Rather
   than inventing a place for that information to live, this RFC points at
   one that already exists as a widely-implemented open standard — JSON
   Schema — and makes the compiler read it.
2. **`ALTER TABLE` should be a real migration, not a trick on a temp
   table.** RFC 0005's `ALTER TABLE` reshapes the ephemeral table for one
   invocation. If the type has a schema, that reshaping needs to *stick* —
   otherwise the next `apply` run against the same type silently reverts
   the column the caller just added, because nothing told the schema about
   it. Writing the schema back is what makes a change durable instead of a
   one-run illusion.
3. **Never reject a bundle for an imperfect or evolving value.** A schema
   declaring `tentativas` as an integer should not make `apply` — or
   `check`, eventually — fail to open a bundle where one document has
   `tentativas: abc`. `parser.py` already preserves scalar spelling
   losslessly and the project's own stance is that OKF does not impose a
   domain taxonomy; a declared type that some data does not yet satisfy has
   to degrade to a diagnostic, not an error.
4. **Reuse a standard instead of inventing one.** An earlier draft of this
   RFC defined its own `fields: {name: {type, nullable, description}}`
   shape. That is a schema language with exactly one implementation, one
   consumer, and none of the tooling, editor support, or `$ref`/composition
   machinery JSON Schema already has. `schema_export.py` already emits
   JSON Schema (and Zod, and Pydantic models) from inferred/cast concept
   data; this RFC lets a bundle *author* that same target format directly,
   instead of `okf-parser` only ever producing it as output.

## Relationship to RFC 0005

RFC 0005 has been accepted and implemented (#30, #32): `apply` materializes
each concept type as a table in an in-memory DuckDB database, executes a
bounded `ALTER TABLE`\* + `UPDATE` script against it, and validates the
*result* (schema diff, row diff, `__okf_*` protection) rather than parsing
the script beforehand, through a hardlink-staged candidate tree, hash
recheck, and atomic per-file replace. Everything below is additional
compilation input and an additional writeback target layered on that
mechanism. It does not change RFC 0005's `--sql` grammar (decision 8), its
`__okf_*` reservations, or its stage-validate-write pipeline — it widens the
*set of files* that pipeline covers (decision 12), the same way a
multi-document `apply` invocation already writes several concept documents
in one pass today.

## Decision

### 1. A specification is two sibling files: `<name>.md` + `<name>.schema.json`

```text
.okf/specs/rotina.md
.okf/specs/rotina.schema.json
```

`--require-spec`'s existing template mechanism (`type_specs.py`,
`spec_relative_path`) is **unchanged** and stays scoped to `check` only —
see decision 2 for how this RFC's compiler actually finds a specification,
which is a different, broader mechanism than `--require-spec`'s derived
path.

The Markdown document's frontmatter carries only a relative reference to
its schema:

```markdown
---
type: OKFTypeSpec
schema: ./rotina.schema.json
---

# Rotina

Representa uma rotina administrativa executada pelo setor. Este documento
concentra a documentação narrativa, exemplos e decisões de modelagem; o
contrato estrutural vive no arquivo referenciado por `schema`.
```

`type: OKFTypeSpec` marks the document as a specification to the compiler
(and to `check`, which already needs to tell a specification apart from a
concept sharing a directory). `schema` is a POSIX-relative path, resolved
against the specification document's own location:

- local files only — no `http(s)://` URL, no `$ref`-style URI, no `#/...`
  JSON Pointer fragment, in v1;
- the resolved path must not escape the bundle root, and a symlink along
  the way must not point outside the bundle — the same boundary
  `bundle.validate_path` already enforces for concept discovery, reused
  here rather than re-implemented;
- a `schema` value that is absent, unreadable, or points outside the
  bundle is a diagnostic (decision 9), never a crash.

A directory-per-type layout (`.okf/specs/rotina/index.md` +
`.okf/specs/rotina/schema.json`) was considered and set aside — see
Alternatives.

### 2. Discovery is semantic and command-agnostic, not template-driven

Every consumer that can compile against or validate a specification —
`apply`, `duckdb`, `schema` (`schema_export.py`), `check`, `inventory`,
`graph` — discovers specifications the **same way**, without being handed
any template or flag:

1. during the bundle's Markdown walk, any document whose frontmatter has
   `type: OKFTypeSpec` is classified as bundle metadata, not a concept —
   the same walk-time classification `.okfignore` and other reserved
   documents already get, requiring no new discovery pass;
2. its `schema` field is resolved to a sibling file (decision 1);
3. that schema's `properties.type.const` (decision 3) associates it with
   the concept type it governs.

A specification is a property of the bundle itself — "just there," the
same way `.okfignore` is — not something a caller must separately point a
command at. This is deliberate: `--require-spec`'s template is per-invocation
and operator-configurable, and is not persisted anywhere in the bundle, so
no command other than `check` could reliably reconstruct it even if this
RFC wanted every consumer to receive it.

`--require-spec` keeps its existing, narrower, `check`-only role
unchanged: an independent assertion that a document exists at one specific
*derived* path for a given type, per its own configured template. It gains
no new normative meaning here, and no other command receives it. The two
mechanisms answer different questions and can disagree without
contradiction — a type can have a specification this RFC's compiler
discovers semantically at any path, without that path ever satisfying a
`--require-spec` template that happens to point elsewhere; `check
--require-spec` can also fail its existence check for a type whose
specification this RFC's compiler already found and is happily compiling.

Reservation follows discovery exactly: only two paths are treated as
bundle metadata per specification — the Markdown document itself and the
exact file its `schema` field resolves to. Both are excluded from
`discover_markdown`'s concept walk; neither is ever counted as a concept,
neither gets a row in any type's table, neither appears in
`inventory`/`graph`.

`.okf/specs/**` as a whole is **not** reserved, and specifications are not
required to live under any particular directory — this RFC only removes
the exact files a real specification names, never an arbitrary directory a
`--require-spec` template happens to resolve under. This is deliberately
narrower than this RFC's own first draft, which reserved the whole tree.

Both files participate in everything that treats them as real files: RFC
0005 decision 5's snapshot, candidate-tree hardlinking, and concurrency
re-check cover them exactly as they cover any other reserved document — a
schema changing underneath a running `apply` invocation is exactly the kind
of concurrent edit that machinery already exists to catch.

`.okf/migrations/**` and `.okf/queries/**`, floated in earlier discussion as
adjacent reserved namespaces, remain out of scope — nothing here needs
them, and reserving a namespace with no defined contents yet would be
speculative.

### 3. `properties.type.const` is the schema's only identity

```json
{
  "type": "object",
  "properties": {
    "type": { "const": "Rotina" }
  }
}
```

There is no separate `defines:` field. The schema's `properties.type.const`
— compared to a concept's `type` the same exact-string way RFC 0005 compares
an `--sql` `UPDATE` target, no slugging — is the only source of identity.
This avoids three competing notions of "which type is this for": the
derived path, a frontmatter field, and a schema keyword. `type_slug()` is
not injective (the same fact that ruled out slug-named tables in RFC 0005),
so the derived `.md` path alone still cannot say which of two colliding
type spellings a specification is for; `properties.type.const` is what
resolves that, exactly as `defines:` would have in the first draft.

A specification that omits `properties.type.const` entirely is a
diagnostic (decision 9) regardless of where it lives — identity is not
optional. When a specification happens to sit at the exact path a
`--require-spec` template derives for some type, and its `const` disagrees
with that type, that mismatch is also a diagnostic — but this is a
`--require-spec`-specific check (decision 2), not a property every
specification is held to: a specification discovered semantically at a
path unrelated to any template has no derived path to compare against in
the first place, only its own `const`.

`$id`, if present, is left for future schema-registry / `$ref` use and
carries no identity meaning in this RFC. Reusing it as identity was
considered and rejected — it conflates "which OKF type does this govern"
with a JSON Schema convention meant for cross-schema referencing.

### 4. The schema file is JSON Schema Draft 2020-12; three "type"s

The schema is a real JSON Schema document, not a look-alike:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:okf:schema:rotina",
  "type": "object",
  "description": "Rotina administrativa executada pelo setor.",
  "properties": {
    "type": { "const": "Rotina" },
    "title": {
      "type": "string",
      "description": "Título identificador da rotina."
    },
    "tentativas": {
      "type": ["integer", "null"],
      "description": "Número de tentativas realizadas.",
      "x-okf-duckdb-type": "BIGINT"
    },
    "valor": {
      "type": ["number", "null"],
      "description": "Valor de referência da rotina.",
      "x-okf-duckdb-type": "DECIMAL(18,4)"
    },
    "registrado_em": {
      "type": ["string", "null"],
      "format": "date-time",
      "description": "Momento em que a rotina foi registrada."
    }
  },
  "required": ["type", "title"],
  "additionalProperties": true
}
```

Worth stating plainly, because it is easy to misread: three different
keywords named `type` appear across this pair of files, at three different
levels, meaning three different things —

1. `type: OKFTypeSpec` (Markdown frontmatter) identifies the `.md` document
   itself as a specification;
2. `"type": "object"` (schema root) is JSON Schema's own structural keyword,
   describing what kind of value the schema validates;
3. `properties.type.const` (schema property) identifies which OKF concept
   `type` — the frontmatter field every concept document has — this schema
   governs.

None of the three is optional to get right, and none should be conflated
with another when reading or writing a specification.

### 5. The v1 profile: a small, explicit subset — everything else preserved

`okf-parser` does not implement JSON Schema Draft 2020-12 in full. Adding a
`$ref`/`$defs` resolver, `oneOf`/`allOf`/`anyOf` composition, `pattern`, and
custom `format` validators is a large surface with no current consumer;
this RFC scopes v1 down to exactly the keywords the compiler interprets:

- at the schema root: `type: "object"`, `properties`, `required`,
  `description`;
- per property: `type` (a single JSON Schema primitive, or the restricted
  union `[<primitive>, "null"]`), `description`, `format` (only `date` and
  `date-time` are interpreted), `items` (only for a homogeneous array of
  scalars), `const` (special-cased only at `properties.type.const`, per
  decision 3), and `x-okf-duckdb-type`.

Every other keyword — `$id`, `$ref`, `$defs`, `enum`, `pattern`, `oneOf`,
`allOf`, `anyOf`, `default`, and anything else valid in Draft 2020-12 — is
**semantically preserved and exported as declared, but not acted on** by
the v1 compiler: no diagnostic is derived from it, no DuckDB decision
depends on it. "Ignored" means "not yet load-bearing," not "invalid" or
"stripped." `enum` and `pattern` were deliberately left out of v1 even
though parsing them is simple — the open problem is not reading them, it
is defining one coherent, advisory-consistent meaning for them across
`check`, `apply`, DuckDB, and every schema exporter at once; that is
future-RFC work, not something to half-commit to here.

"Semantically preserved" is a deliberately weaker promise than
byte-identical: a schema written back by `apply` (decision 10) goes through
a plain JSON encode-decode round trip, which keeps every member's value and
every property's own key order, but is **not** guaranteed to reproduce the
original file's exact whitespace, indentation width, key ordering at
levels `apply` didn't touch beyond the natural effect of an unmodified
Python `dict` preserving insertion order, or a number's original lexical
spelling (`1.50` may re-serialize as `1.5`). Canonical output is UTF-8,
2-space indent, one trailing newline, `ensure_ascii=False`. A future RFC
that wants byte-for-byte JSON preservation — the way RFC 0005's YAML loader
already achieves for concept documents — would need to adopt a JSON
round-trip library equivalent to `ruamel.yaml`'s role for YAML; none is a
dependency today, and this RFC does not add one.

### 6. Divergence from the schema is advisory, never a block

JSON Schema tooling conventionally treats a `required`/`type` mismatch as a
hard validation failure. That is **not** this RFC's policy. Consistent with
RFC 0005 decision 1c and `type_specs.py`'s own advisory-by-default
precedent:

- `required` states that a key is expected to be present; presence is
  independent of nullability — a key can be required *and* nullable
  (`["string", "null"]`, still in `required`), or optional and non-null;
  absence and an explicit `null` value remain distinct states throughout;
- a document that violates `required`, or whose value does not cast to its
  property's declared `type`, is **never** rejected — the bundle still
  opens, the table still builds. `apply`/`check`/`duckdb`/`schema` emit an
  advisory diagnostic (`OKF0xx`, see decision 9 for exactly which surface
  escalates it and how) naming the declared type, the physical type
  actually used, the offending value, and its document;
- a value that fails to cast makes that field's column fall back to
  `VARCHAR` for the affected row's type family the same way RFC 0005
  decision 1c already handles blind inference — a caller-declared
  expectation that some data hasn't caught up with yet is not, by itself,
  an OKF conformance defect.

`required` never produces a DuckDB `NOT NULL` constraint. Doing so would
turn an advisory contract into a physical barrier that blocks materializing
the very rows the diagnostic exists to describe.

### 7. Physical type mapping

| JSON Schema | DuckDB default |
| --- | --- |
| `string` | `VARCHAR` |
| `integer` | `BIGINT` |
| `number` | `DOUBLE` |
| `boolean` | `BOOLEAN` |
| `string` + `format: date` | `DATE` |
| `string` + `format: date-time` | `TIMESTAMPTZ` |
| `array` + `items.type: string` | `VARCHAR[]` |

`date-time` maps to `TIMESTAMPTZ`, not `TIMESTAMP`: JSON Schema's
`date-time` format is RFC 3339, an instant with an offset — mapping it to a
timezone-naive type would silently discard information. A caller who
specifically wants a timezone-naive local timestamp declares it explicitly:

```json
{ "type": "string", "format": "date-time", "x-okf-duckdb-type": "TIMESTAMP" }
```

`x-okf-duckdb-type` is the escape hatch for every case the default table
doesn't cover precisely — `DECIMAL(18,4)` instead of `DOUBLE`, a narrower
integer width, and so on. The JSON Schema `type` expresses the logical
category; `x-okf-duckdb-type`, when present, expresses the exact physical
representation.

### 8. Comments: schema prose to catalog, never through `--sql`

```sql
COMMENT ON TABLE "Rotina" IS 'Rotina administrativa executada pelo setor.';
COMMENT ON COLUMN "Rotina".registrado_em IS 'Momento em que a rotina foi registrada.';
```

The schema's own `description` (root) becomes the table's `COMMENT ON
TABLE`; each property's `description` becomes its column's `COMMENT ON
COLUMN`. Deliberately **not** the Markdown body — the body can be long,
carry examples and rationale that don't belong in a catalog comment; the
schema's `description` is the short, structural summary meant for exactly
this purpose. The Markdown document remains narrative documentation only:
long-form explanation, examples, modeling decisions, nothing the compiler
reads for typing or comments beyond confirming `type: OKFTypeSpec` and
resolving `schema`.

`COMMENT ON` does **not** enter `--sql`'s grammar. RFC 0005's `apply`
continues to accept only zero-or-more leading `ALTER TABLE` statements plus
exactly one trailing `UPDATE` — unchanged. A description is edited by
editing the schema file's `description` fields directly, not through a
script run against `apply`. This was the first point resolved in RFC 0006
review: extending `apply`'s SQL grammar for comment writes would contradict
RFC 0005's just-implemented contract for no clear benefit, since editing
the schema file directly is no heavier.

### 9. Diagnostics

All advisory by default, distinct cases:

- `schema` field absent from `OKFTypeSpec` frontmatter — malformed
  specification;
- the file `schema` resolves to does not exist, or resolves outside the
  bundle — broken reference;
- the referenced file is not valid JSON — unreadable schema;
- keywords outside the v1 profile (decision 5) — preserved, no diagnostic
  by itself;
- `properties.type.const` absent — missing identity;
- two `OKFTypeSpec` documents whose `schema` resolves to the same file —
  ownership conflict;
- two schema files both declaring `properties.type.const` for the same OKF
  type — ambiguity.

Escalation is per-surface, matching what already exists rather than
inventing one flag for all of them: `check --require-spec` additionally
reports its own existence-mismatch case (decision 2's "specification found
at the derived path but its `const` disagrees") and escalates every
`OKFTypeSpec`-related diagnostic through `check`'s existing
`--normative-spec` flag — there is no bare `--normative` flag today, and
this RFC does not invent one. `apply`, `duckdb`, and `schema` surface the
same diagnostics through whatever advisory-reporting mechanism each
already has (`apply`'s `validation` payload, etc.); none of them gain a new
escalation flag by this RFC.

### 10. `ALTER TABLE` writes back to the schema, when one exists

When the type an `ALTER TABLE` targets has a schema file:

- `ADD COLUMN "prazo" BIGINT` creates `schema.properties.prazo`, with `type`
  set to the nearest logical JSON Schema type for `BIGINT` (`integer`).
  **It never adds `prazo` to `required`** — `ALTER TABLE ADD COLUMN` has no
  way to express "and this must always be present" in RFC 0005's grammar,
  so inventing that obligation here would be an unrequested escalation.
  When the exact DuckDB type isn't the default physical mapping for that
  logical type (decision 7) — e.g. `ADD COLUMN "valor" DECIMAL(18,4)`,
  whose default would have been `DOUBLE` — the sync also writes
  `x-okf-duckdb-type` explicitly, or the next compile would silently lose
  the declared precision;
- `DROP COLUMN "prazo"` removes `properties.prazo` and, if present, `prazo`
  from `required`;
- `RENAME COLUMN "prazo" TO "prazo_dias"` renames the `properties` key,
  updates its entry in `required` if present, and preserves every other
  keyword on that property node unchanged — including ones outside the v1
  profile (decision 5), semantically preserved per that decision's precise
  round-trip guarantee;
- descriptions are **not** part of this sync (decision 8) — they change by
  editing the schema file directly.

### 11. Value writeback: safe scalars now, explicit failure otherwise

`apply`'s `UPDATE` writes Python values back to YAML scalars. This RFC adds
writeback for a deliberately limited, incremental set of physical types —
the ones round-trippable to a YAML scalar without any precision or lexical
ambiguity:

**Writable in v1:** `VARCHAR` (string), the DuckDB integer family, `BOOLEAN`,
`DATE`, `TIMESTAMP`, `TIMESTAMPTZ`.

Calling `TIMESTAMPTZ`/`TIMESTAMP` "round-trippable without ambiguity" only
holds with an explicit canonical serialization, since DuckDB does not
preserve the exact offset a value was written with. This RFC fixes one:

- `TIMESTAMPTZ` is normalized to UTC and serialized as RFC 3339 with a
  literal `Z` suffix (never a numeric `+00:00` offset); fractional seconds
  are emitted at fixed microsecond precision when the value has any
  sub-second component, and omitted entirely otherwise — e.g.
  `2026-08-06T14:30:00Z` or `2026-08-06T14:30:00.125000Z`, never
  `2026-08-06T14:30:00.125Z` or a value carrying its original,
  pre-normalization offset;
- `TIMESTAMP` (no offset, `x-okf-duckdb-type` opt-in per decision 7) is
  serialized the same way minus the `Z`, as a naive local timestamp:
  `2026-08-06T14:30:00` / `2026-08-06T14:30:00.125000`;
- `DATE` is serialized as `YYYY-MM-DD`.

Every value in this section is written as a YAML scalar the round-trip
loader parses back to the same DuckDB value on the next `apply` — the
canonical form above, not whatever lexical form a particular document
happened to use before a write touched it.

**Read-only in v1** — the column materializes and casts correctly, and
`ADD`/`DROP`/`RENAME COLUMN` sync to the schema exactly as decision 10
describes, but an `UPDATE` that would change one of these columns' values
fails, explicitly and by name, rather than degrading to `NULL`, a removed
key, or a silently stringified value:

- `DOUBLE`/`number` — float round-tripping through Python and back to a
  YAML scalar can shift lexical representation or precision;
- `DECIMAL` — writing it as a float would misrepresent it; writing it as a
  string would contradict its own declared JSON Schema `number` type;
  neither is safe to pick silently;
- arrays;
- structs, maps, and any composite/union physical type;
- any physical type the serializer does not recognize.

An `UPDATE` attempting to change the value of a non-writable-type column
aborts the whole script with a diagnostic naming the column, its physical
type, and that RFC 0006 v1 can materialize but not write back to it — the
same "reject after execution, by inspecting the result" posture RFC 0005
already uses for protected-column and shape violations, not a new
validation path. `DROP COLUMN` remains possible for any column regardless
of physical type — dropping doesn't require serializing a value.
`RENAME COLUMN` on a non-writable-type column is fine as long as the value
itself is untouched (the original YAML node is preserved, only its key
changes); a script that renames *and* changes such a column's value in the
same run aborts.

Widening writeback to `number`/`DECIMAL`/arrays/composites is left to a
follow-up RFC once a canonical, deterministic serialization for each is
specified — not attempted here.

### 12. Both files share RFC 0005's existing multi-file write mechanism

`apply` already writes an arbitrary number of concept documents in one
invocation, each through the same sequence: build a hardlink-staged
candidate tree, validate the *candidate* bundle baseline-relative, re-check
every touched file's real content hash immediately before replacing it, and
replace one file at a time via `tmp.replace(path)`. This RFC does not
introduce a new commit mechanism — it adds the schema file (and, only if
its own content is affected, the specification Markdown) to that same set
of touched files. `--write` writes the schema and every touched concept
document in the same pass, or writes none of them, exactly as it already
does for N concept documents today.

This RFC makes **no stronger atomicity claim** than RFC 0005 already
makes. There is no single OS-level transaction spanning multiple files
today, for concept documents or for a specification's two files — the
guarantee is: validate the full candidate bundle first, re-confirm no
concurrent edit raced ahead of the candidate build, then perform the real
replacements. A crash between two individual `tmp.replace()` calls is the
same risk profile RFC 0005 already accepts for any multi-document
`apply` run; this RFC widens the file set inside that existing envelope,
it does not narrow or strengthen the envelope itself.

### 13. `duckdb` and `schema` consume the same compiler, normatively

The Motivation section's `schema_export.py` reference is not decorative —
it names the second and third public surfaces this RFC is normative about.
`apply`'s in-memory DuckDB database is ephemeral, discarded at the end of
one invocation; if typed columns and `COMMENT ON` metadata only ever
existed there, this RFC's main payoff would never reach anything a caller
actually keeps. The specification-aware compiler introduced in decisions
4–8 is one shared component (discovered per decision 2), and this RFC
requires it to back every command that materializes or exports a type's
schema, not `apply` alone:

- **`duckdb`** — the existing command that materializes concept types into
  a DuckDB database meant to persist or be exported, as opposed to
  `apply`'s throwaway one — applies the same declared-type mapping
  (decision 7) and issues the same `COMMENT ON TABLE`/`COMMENT ON COLUMN`
  statements (decision 8) into that database, for every type that has a
  specification. A type with no specification still compiles exactly as
  RFC 0005 already defines for `duckdb`, unchanged;
- **`schema`** (`schema_export.py`) — for a type with a specification, its
  schema file becomes the canonical source for that type's exported JSON
  Schema, instead of one derived purely from inferred/cast concept data.
  Properties covered by the specification carry over as declared,
  including every keyword outside the v1 profile (decision 5) that
  `schema_export.py` does not itself interpret but re-emits unchanged, per
  that decision's round-trip guarantee. Properties present in concept data
  but absent from the specification continue to be inferred the way
  `schema_export.py` already does today — a specification narrows what is
  inferred, it does not have to cover every field a type happens to use.
  Zod and Pydantic generation, which are themselves derived from the same
  compiled contract (`schema_contract.py`), inherit this for free rather
  than needing their own awareness of specifications.

`check`, `inventory`, and `graph` are unaffected beyond decision 2's
discovery/reservation and decision 6's advisory diagnostics — none of them
materialize or export a schema, so decisions 7 and 8 have nothing to plug
into for them.

## Alternatives considered

### A bundle-invented `fields: {name: {type, nullable, description}}` shape (first draft of this RFC)

Rejected. It is a schema language with exactly one implementation and no
tooling of its own, duplicating most of what JSON Schema already
standardizes — properties, required, nullability via union types, nested
structure, descriptions, `$ref`/composition for later reuse. Adopting JSON
Schema instead means `schema_export.py` can eventually treat a declared
schema as a source rather than something it only ever produces.

### Embedding the JSON Schema inline in the specification's YAML frontmatter

Considered, and initially preferred over a sibling file. Rejected in favor
of the two-file form because: an external `.schema.json` is directly
usable by any JSON Schema tool (editors, validators, `$ref` resolution)
without extracting it from YAML frontmatter first; it can be versioned,
diffed, and consumed independently of the Markdown; and `apply`'s writeback
(decision 10) becomes a plain JSON file edit instead of mutating a deeply
nested block inside a YAML document. The real cost — a specification is
now two files instead of one — is paid for with the explicit reservation
and diagnostic rules in decisions 1, 2, and 9, rather than left implicit.

### Directory-per-type specification layout (`.okf/specs/rotina/index.md` + `schema.json`)

Considered, for the same reason a `pyproject.toml`/`Cargo.toml`-adjacent
project sometimes grows into a directory once fixtures or migrations show
up. Rejected for v1: it would change `--require-spec`'s already-shipped
template contract, which today resolves one file path via `{slug}`
substitution, into something that needs to resolve a directory plus a
conventional filename. The flat sibling-file form (decision 1) requires no
change to that contract. Revisit once directory-worthy content — fixtures,
worked examples, migration history — is a real, not speculative, need.

### Data Package Table Schema as the structural contract

Considered — it is close to the relational problem directly (`fields`,
`type`, `constraints`, `primaryKey`, `foreignKeys`) and already has YAML
tooling via Frictionless. Not adopted as the core contract: OKF documents
are objects with frontmatter, not table rows, and the project also wants
one contract that exports cleanly to JSON Schema, Zod, and Pydantic
simultaneously — Table Schema is a plausible *inspiration* for a later
primary-key/foreign-key extension, not a replacement for JSON Schema as the
per-type contract.

### LinkML as the structural contract

Considered — YAML-first, and rich (classes, slots, inheritance, multiple
generator targets). Rejected as disproportionate: adopting it means fitting
OKF into LinkML's own metamodel (classes, slots, imports, generators) for a
problem that a small, explicit JSON Schema profile already solves without a
new dependency or a new mental model.

### Reject the bundle when a value doesn't match its declared type

Rejected, for the same reason `OKF010` is advisory by default (decision 6):
a bundle mid-adoption legitimately has data that predates its schema, or a
schema that's aspirational ahead of a cleanup migration. Hard-failing
`apply` (or, later, `check`) on that would make declaring a type strictly
more dangerous than not declaring one.

### Make spec writeback opt-in via a flag, rather than automatic whenever a schema exists

Rejected, unchanged from the first draft's reasoning: the point of decision
10 is closing motivation case 2 (`ALTER TABLE` silently reverting itself on
the next run) without a second flag to remember every time.

## Open questions

- Exact `OKF0xx` diagnostic codes for every case in decision 9 — needs to
  fit the existing numbering in `type_specs.py`/`schema_contract.py`
  rather than being assigned here in isolation.
- Whether validating that a schema file is itself syntactically valid
  Draft 2020-12 needs a real JSON Schema validation library (none is a
  current dependency) or whether checking only the v1 profile's own
  keywords (decision 5) is sufficient for now.
- Ambiguous casts: what makes a value "convertible" for decision 6's
  try-without-forcing rule is underspecified beyond DuckDB's own
  `TRY_CAST` semantics — whether that is sufficient, or OKF wants stricter
  rules, is not resolved here.
- `enum`, `pattern`, and composition (`oneOf`/`allOf`/`anyOf`) are
  explicitly deferred (decision 5) to a follow-up RFC once a coherent,
  advisory-consistent meaning across `check`/`apply`/DuckDB/exporters is
  worked out.
- Widening value writeback (decision 11) to `number`/`DECIMAL`/arrays once
  a canonical, deterministic YAML serialization is specified for each.
- Whether `list[T]`/array read support needs its own conformance fixtures
  shared between the Python and TypeScript runtimes, once a TypeScript
  implementation of this RFC is in scope.

This RFC depends on RFC 0005, now accepted and implemented (#30, #32), for
the `ALTER TABLE`/candidate-tree/atomic-replace mechanism it extends.
