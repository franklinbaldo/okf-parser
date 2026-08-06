---
type: RFC
title: Spec-driven relational typing and bidirectional spec sync
status: proposed
description: Let an optional linked JSON Schema file declare a type's schema and documentation, compiled into DuckDB types and COMMENT ON metadata, with ALTER TABLE writing back to the schema that declared it
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

The specification is a linked pair of files, not a single document —
"linked," not "sibling": `schema` (decision 1) is a POSIX-relative path
resolved against the Markdown document's own location, so the two files
commonly sit side by side but are not required to; a bundle is free to
keep schema files under their own subdirectory (`schema:
./schemas/rotina.schema.json`) if that suits its layout:

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

### 1. A specification is two linked files: `<name>.md` + `<name>.schema.json`

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
2. its `schema` field is resolved to its linked file (decision 1);
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

**The schema file must validate against the JSON Schema Draft 2020-12
meta-schema itself.** Calling it "a real JSON Schema document" (this
decision's own opening line) is a claim, not a hope — it has to be
checked, not merely assumed of whatever JSON happens to parse. A file that
is syntactically valid JSON but is not a well-formed Draft 2020-12 schema
— an `enum` whose value isn't an array, a `$ref` that isn't a string, a
malformed `additionalProperties` shape, and any other meta-schema
violation — is treated exactly as unreadable JSON already is (decision 9):
a diagnostic that makes the specification ineligible, whole-specification
fallback, same as today. This closes what an earlier revision of this RFC
left as an open question ("does validating the schema file need a real
JSON Schema library") — it does, and this RFC commits to that cost rather
than leaving two conformant implementations free to disagree about which
malformed files are tolerated. The check is meta-schema conformance of the
*schema document itself* only — it has no relationship to decision 6's
advisory try-without-forcing validation of *concept data* against that
schema, which stays exactly as defined.

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
**not acted on** by the v1 compiler: no diagnostic is derived from it, no
DuckDB decision depends on it. "Ignored" means "not yet load-bearing," not
"invalid" or "stripped." `enum` and `pattern` were deliberately left out of
v1 even though parsing them is simple — the open problem is not reading
them, it is defining one coherent, advisory-consistent meaning for them
across `check`, `apply`, DuckDB, and every schema exporter at once; that is
future-RFC work, not something to half-commit to here.

**The preservation promise itself only holds for the schema file and JSON
Schema output — not for every projection.** "Semantically preserved and
exported as declared" is achievable when the target is the schema file
itself (decision 10's writeback) or `schema`'s JSON-format output
(decision 15), because both re-emit JSON Schema, the same vocabulary the
unread keywords are already written in — no interpretation is needed to
pass them through. It is **not** achievable for Zod or Pydantic output: a
`oneOf` or a `pattern` has no representation in Zod/Pydantic's own type
systems without the compiler interpreting it, which decision 5 explicitly
does not do in v1. Zod and Pydantic generation therefore compile **only
the v1 profile** — a property using an out-of-profile keyword still
generates a field from whatever the v1 profile *does* say about it (its
`type`, nullability, `description`), silently narrower than the full
declared contract in exactly the keywords this decision lists as ignored.
This is a real, documented loss specific to those two projections, not an
oversight: a caller who needs the full declared contract, keywords and
all, reads the schema file directly or uses `schema`'s JSON-format output,
not Zod or Pydantic.

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

**Not every v1-profile-valid property has a resolvable physical type.**
This table gives exactly one bare array default (`array` + `items.type:
string` → `VARCHAR[]`); every other homogeneous scalar array decision 5's
profile otherwise allows (`items.type: integer`, `items.type: boolean`,
and so on) has no default here and needs an explicit `x-okf-duckdb-type`
on the property — decision 10 states the matching rule for the reverse
(`ADD COLUMN`) direction. A property expressed only through a keyword this
decision doesn't map at all — for instance, one that relies solely on
`$ref`/`$defs` for its shape, with no interpretable `type` of its own — is
in the same position. Neither case is a malformed schema; both are simply
properties the v1 profile can describe structurally (and decision 5
preserves whatever else they declare) without this decision being able to
say what DuckDB column they become. Both resolve through decision 9's
single-property fallback: no diagnostic-worthy defect in the schema, but
that one property compiles, for materialization only (`apply`'s ephemeral
table, `duckdb`'s persistent view), exactly as if it hadn't been declared —
while `schema`'s JSON output and `apply`'s writeback keep emitting it
exactly as authored, per decision 5.

**Normalization to DuckDB's catalog-normalized spelling applies to
validation, diagnostics, and any `x-okf-duckdb-type` this RFC's own
compiler writes for the first time — never to rewriting a value someone
already authored.** Decision 10's `RENAME COLUMN` preserves every keyword
on a property node, `x-okf-duckdb-type` included, byte-for-byte — the two
rules don't conflict because they apply to different moments: a spec
author writing `"x-okf-duckdb-type": "decimal (18, 4)"` gets that exact
string echoed back by a `RENAME` that never touches the value, and gets
`DECIMAL(18,4)` used for the actual `CREATE`/`ALTER` DDL and for any
diagnostic that names the type. Only when this decision's compiler is the
one *writing* an `x-okf-duckdb-type` for the first time — decision 10's
reverse-mapping table, synthesizing one for an `ADD COLUMN` whose exact
type differs from its logical type's default — is the catalog-normalized
spelling what gets written.

**`x-okf-duckdb-type` is never spliced into DDL as raw text.** A schema
file is bundle data, not caller-trusted code — unlike an `--sql` script,
which is the operator's own input, a schema's `x-okf-duckdb-type` string
can come from anyone with write access to the bundle. Before it is used
anywhere a column type is needed (`CREATE TABLE`/`ALTER TABLE` in `apply`'s
ephemeral database, a `TRY_CAST` target in `duckdb`'s persistent per-type
views per decision 14),
the compiler validates it in isolation — parsed by DuckDB itself as
exactly one type expression in a synthetic, side-effect-free context (e.g.
casting a literal `NULL` to it), never concatenated into a multi-statement
string first. Parsing that succeeds as anything other than one type
expression — extra tokens, a statement separator, a second statement — is
rejected as a diagnostic (decision 9), the same as any other malformed
schema value; it never reaches a real `CREATE`/`ALTER`. On success, the
compiler uses **DuckDB's own catalog-normalized spelling** of that type
(what `DESCRIBE`/the catalog reports back, e.g. `DECIMAL(18,4)` regardless
of whitespace or case in the source) everywhere downstream — comments,
diagnostics, decision 10's writeback — never the caller's original string
verbatim a second time.

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

### 9. Diagnostics, and what "advisory" actually compiles to for a broken specification

All advisory by default, distinct cases:

- `schema` field absent from `OKFTypeSpec` frontmatter — malformed
  specification;
- the file `schema` resolves to does not exist, or resolves outside the
  bundle — broken reference;
- the referenced file is not valid JSON — unreadable schema;
- the referenced file is valid JSON but fails Draft 2020-12 meta-schema
  validation (decision 4) — malformed schema;
- keywords outside the v1 profile (decision 5) — preserved, no diagnostic
  by itself;
- `properties.type.const` absent — missing identity;
- two `OKFTypeSpec` documents whose `schema` resolves to the same file —
  ownership conflict;
- two schema files both declaring `properties.type.const` for the same OKF
  type — ambiguity;
- an `x-okf-duckdb-type` value that fails decision 7's isolated-parse
  validation — malformed physical type, never reaching a real `CREATE`/
  `ALTER`;
- a property with no resolvable physical type — no bare array default and
  no `x-okf-duckdb-type` override, or no mapped `type` at all (decision 7)
  — unmaterializable property;
- `schema`'s `--cast` naming a property already covered by a declared
  schema (decision 15) — cast conflict; the declared type is exported, the
  cast is not applied;
- a concept document holding a field a closed specification
  (`additionalProperties`/`unevaluatedProperties: false`) doesn't declare
  (decision 15) — undeclared field under a closed schema.

**"Advisory" is not just a severity label — it is a defined fallback every
consumer applies the same way.** Saying a diagnostic is advisory means
nothing on its own unless it's also settled what actually gets compiled
when a specification is broken; leaving that implicit would let two
conformant implementations legitimately disagree, and would leave `apply`
unable to know which file, if any, its writeback (decision 10) targets.
So:

Three distinct degrees of fallback, not one — collapsing them into a single
"ineligible" bucket is exactly what produced a contradiction with decision
15 in an earlier revision of this decision, so they are named separately:

- **whole-specification fallback**, for a failure nothing about the
  specification's own content can localize — a missing `schema` field, a
  broken reference, unreadable JSON, a Draft 2020-12 meta-schema
  validation failure (decision 4), missing `properties.type.const`, or
  either half of an ownership conflict or an identity ambiguity. These
  make the **affected specification ineligible**: the type it would have
  governed compiles, in `apply`, `duckdb`, and `schema` alike, **exactly
  as it would with no specification at all** (RFC 0005's plain `VARCHAR`
  inference, `schema`'s existing inferred/cast output), and `apply`'s
  writeback (decision 10) does not target it — an `ALTER TABLE` against
  that type reshapes only the ephemeral table, the same as any type with
  no specification, because there is no well-formed specification to
  write into;
- **single-property fallback**, for two distinct causes that both leave
  the property itself fully declared and eligible, only its physical
  materialization affected: (a) a malformed `x-okf-duckdb-type`, or (b) a
  property with no resolvable physical type at all — an array whose item
  type has no bare default and no `x-okf-duckdb-type` override (decision
  7), or a property expressed only through a keyword this RFC's v1 profile
  doesn't map to a physical type in the first place (decision 7). Either
  way: materialization (`apply`'s ephemeral table, `duckdb`'s persistent
  view) falls back to treating that one property as if the specification
  hadn't declared it at all — for (a), decision 7's bare default physical
  type for the property's declared logical `type`/`format` is used
  instead, as if `x-okf-duckdb-type` had never been written; for (b),
  there is no default to fall back to, so the property is simply not
  compiled as a typed column, exactly like a field with no specification.
  In both cases the property's declared `type`, `format`, and
  `description` are used exactly as declared everywhere else that isn't
  materialization — nothing about the property's own validity is in
  question. `schema`'s JSON output and `apply`'s writeback (decision 5)
  still re-emit the property **exactly as authored, any unparseable or
  physically-unmappable member included** — neither path interprets these
  values, so there is nothing for either of them to reject; only
  materialization, which does interpret them, degrades;
- **no fallback at all**, for a `--cast` naming a property a schema
  already declares. This is not a defect in the specification to degrade
  around — the specification is exactly as valid as it was — so nothing
  about the property changes: decision 15 already settles this
  precisely (declared property wins outright, the cast is not applied),
  and this decision does not restate or loosen that outcome. Only the
  cast itself is diagnosed.

This is exhaustive for v1: every case above resolves to "ignore the
unusable part, compile the rest exactly as declared," never to a hard
failure and never to discarding more than the one thing that was actually
broken. If a future case is found that genuinely cannot degrade this way,
it must be named explicitly and removed from the advisory category by that
future RFC — decision 15's `SchemaExportError` is not, and must not
become, a back door for a specification-content failure to bypass this
fallback (see decision 15).

Escalation is per-surface, matching what already exists rather than
inventing one flag for all of them: `check --require-spec` additionally
reports its own existence-mismatch case (decision 2's "specification found
at the derived path but its `const` disagrees") and escalates every
`OKFTypeSpec`-related diagnostic through `check`'s existing
`--normative-spec` flag — there is no bare `--normative` flag today, and
this RFC does not invent one. `apply` and `duckdb` surface the same
diagnostics through whatever advisory-reporting mechanism each already has
(`apply`'s `validation` payload, etc.). `schema`'s transport is decision
15's own: a `"diagnostics"` key in JSON mode, stderr in Zod mode, exit code
unaffected in both. None of them gain a new escalation flag by this RFC.

### 10. `ALTER TABLE` writes back to the schema, when one exists

**`ADD COLUMN` of a type this decision's reverse mapping cannot represent
stays rejected, exactly as RFC 0005 already rejects it.** RFC 0005 v1
requires every `ADD COLUMN` to be `VARCHAR`, full stop (`_check_result_schema`).
This RFC lifts that restriction **only** for a type that has a schema file,
and **only** for the exact DuckDB types the table below can turn back into
a v1-profile JSON Schema property — never unconditionally. A type with no
schema file keeps RFC 0005's original VARCHAR-only restriction exactly as
defined, unchanged. An `ADD COLUMN` of any DuckDB type not in this table —
`STRUCT`, `MAP`, `UNION`, `ENUM`, `BLOB`, `INTERVAL`, `UUID`, and anything
else this RFC does not name — is rejected the same way, whether or not a
schema exists, because there is no way to sync it into the v1 profile
without inventing representation this RFC does not define. This closes the
gap two conformant implementations could otherwise fill in differently.

| `ADD COLUMN` DuckDB type | Written to `properties.<name>` |
| --- | --- |
| `VARCHAR` | `{"type": "string"}` |
| `BOOLEAN` | `{"type": "boolean"}` |
| any DuckDB integer type (`TINYINT` … `HUGEINT`, signed or unsigned) | `{"type": "integer"}`, `x-okf-duckdb-type` added unless the exact type is `BIGINT` (decision 7's default) |
| `DOUBLE`/`REAL`/`FLOAT` | `{"type": "number"}`, `x-okf-duckdb-type` added unless the exact type is `DOUBLE` |
| `DECIMAL(p,s)` | `{"type": "number", "x-okf-duckdb-type": "DECIMAL(p,s)"}` — always annotated, since `DECIMAL` is never decision 7's bare default for `number` |
| `DATE` | `{"type": "string", "format": "date"}` |
| `TIMESTAMPTZ` | `{"type": "string", "format": "date-time"}` |
| `TIMESTAMP` | `{"type": "string", "format": "date-time", "x-okf-duckdb-type": "TIMESTAMP"}` — annotated, since `TIMESTAMPTZ` is decision 7's bare default for `date-time` |
| `VARCHAR[]` | `{"type": "array", "items": {"type": "string"}}` |
| any other `<scalar>[]` covered by a row above | see below — `items` carries only the logical scalar schema; the array's own physical type is always annotated at the property level |

**Nullability is shaped differently for a scalar property than for an
array property**, and decision 6's `[<primitive>, "null"]` union only
describes the scalar case — a JSON Schema `type` union cannot contain an
object (`items`'s schema), so an array property is never written as
`"type": [{"type": "array", ...}, "null"]`. Instead, an array property's
own `type` keyword is the union `["array", "null"]`, sitting alongside
`items` as a sibling keyword, exactly like a plain (non-nullable) array
already looks except for the added `"null"`:

```json
// prazo_dias BIGINT[]
{
  "type": ["array", "null"],
  "items": { "type": "integer" },
  "x-okf-duckdb-type": "BIGINT[]"
}
```

```json
// registros DATE[]
{
  "type": ["array", "null"],
  "items": { "type": "string", "format": "date" },
  "x-okf-duckdb-type": "DATE[]"
}
```

```json
// tags VARCHAR[] — the one array shape with a bare physical default
{
  "type": ["array", "null"],
  "items": { "type": "string" }
}
```

Two rules make this unambiguous rather than case-by-case:

- **`items` carries only the item's logical schema** (`type`, and
  `format` when relevant) — never an `x-okf-duckdb-type` of its own. A
  physical annotation for one array element type does not compose
  cleanly with a physical annotation for the array type as a whole, so
  this decision puts the array's physical type in exactly one place;
- **the array's physical type is annotated at the property level whenever
  it is anything other than `VARCHAR[]`** — not "whenever the item type
  differs from its own row's default," which would wrongly let `BIGINT[]`
  go unannotated on the theory that `integer` is already `BIGINT`'s
  default row. Decision 7 defines exactly one bare array default —
  `array` + `items.type: string` → `VARCHAR[]` — and nothing else; every
  other array physical type, `BIGINT[]`/`DATE[]`/`TIMESTAMPTZ[]`
  included, has no default to fall back to and is therefore always
  annotated, the same "no default exists, so always write it" logic
  `DECIMAL(p,s)` already gets in the scalar table above.

A scalar property's nullability is unaffected by any of this — it stays
exactly `[<primitive>, "null"]` as decision 6 already defines, since a
scalar mapping is never an object.

Every mapped property is nullable by the rule matching its own shape
above — `required` (decision 6) governs presence, not nullability, so it
has no bearing on this at all, and `ADD COLUMN` never adds a column to
`required` in the first place (below).

- `ADD COLUMN "prazo" BIGINT` creates `schema.properties.prazo` per the
  table above. **It never adds `prazo` to `required`** — `ALTER TABLE ADD
  COLUMN` has no way to express "and this must always be present" in RFC
  0005's grammar, so inventing that obligation here would be an
  unrequested escalation;
- `DROP COLUMN "prazo"` removes `properties.prazo` and, if present, `prazo`
  from `required`;
- `RENAME COLUMN "prazo" TO "prazo_dias"` renames the `properties` key,
  updates its entry in `required` if present, and preserves every other
  keyword on that property node unchanged — including ones outside the v1
  profile, per decision 5's schema-file preservation guarantee (not its
  narrower Zod/Pydantic one, which does not apply to writeback at all);
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
describes, but persisting a real value through one of these columns fails,
explicitly and by name, rather than degrading to `NULL`, a removed key, or
a silently stringified value:

- `DOUBLE`/`number` — float round-tripping through Python and back to a
  YAML scalar can shift lexical representation or precision;
- `DECIMAL` — writing it as a float would misrepresent it; writing it as a
  string would contradict its own declared JSON Schema `number` type;
  neither is safe to pick silently;
- arrays;
- structs, maps, and any composite/union physical type;
- any physical type the serializer does not recognize.

**The guard is on the compiled diff against the originally-authored
document, not on a blanket scan of the final table.** An earlier revision
of this decision said "abort if any non-writable column holds a non-`NULL`
value in the final table" — too broad: a bundle that already has a
populated `DECIMAL` column (materialized read-only, per this decision)
would then fail *any* `apply` run against that type, even one that only
touches an unrelated field and never reserializes that column at all. It
also contradicted the rename exception immediately below it, which relies
on the original YAML node never being touched in the first place.

The actual rule follows RFC 0005's own value-compile logic
(`_compile_row_diff`), which is already, deliberately, "blind to *how* the
final state was reached" and already compares each column's final value to
what the document originally authored — decision 11 does not need a
separate mechanism, only to extend that existing comparison to non-writable
physical types instead of assuming every compiled value is a string:

- for a column kept under its current name — whether its value changed via
  the trailing `UPDATE`, or structurally via `ADD COLUMN ... DEFAULT` or a
  same-run `DROP`+`ADD` reset of the same name — the compiler compares the
  row's final value to the value the document originally authored for that
  key, the same comparison `_compile_changed_field_value` already makes.
  **Only when that comparison says a new, different, non-null value must be
  persisted, and that value's physical type is non-writable, does the
  script abort.** A row whose non-writable-type column value is identical
  to what was already authored needs no compiled entry at all — the
  original document scalar is never revisited, so it never trips this
  guard, regardless of how many other rows in the same table did change;
- for a column that is the final name of a rename chain (decision 10)
  targeting a non-writable physical type, the compiler never re-serializes
  a DuckDB value for it in the first place — the original YAML scalar node
  carries over unchanged under the new key, exactly as `_compile_field_value`
  already does for a plain rename (it only asks whether the *old* key was
  ever authored, never re-derives the value from DuckDB). A pure rename of
  a non-writable-type column therefore never aborts. Only when the same
  script also changes that column's value — through the trailing `UPDATE`
  targeting the new name, or a structural reset layered on top of the
  rename — does the general new-value rule above apply, and abort;
- `DROP COLUMN` of a non-writable-type column never aborts — dropping
  deletes a key, it never persists a value, which decision 10 already
  handles structurally.

`ADD COLUMN <non-writable type>` with no `DEFAULT` (or `DEFAULT NULL`)
never aborts either, by the same rule: every row's originally-authored
value for that (previously nonexistent) key was absent, and its final
value is `NULL` — no new non-null value, no compiled entry, nothing to
abort over. `ADD COLUMN <non-writable type> DEFAULT <non-null literal>`
aborts the instant it applies to any pre-existing row, since that row's
compiled entry would need to go from absent to a genuinely new non-null
value of a non-writable type — the same "reject after execution, by
inspecting the result" posture RFC 0005 already uses for protected-column
and shape violations, not a new validation path.

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

- **`duckdb`** (`attach_okf`) — today persists exactly four fixed,
  generic tables (`concepts`, `links`, `reserved`, `diagnostics`), with
  every concept's frontmatter packed into `concepts.frontmatter_json` —
  there is no existing per-type relation to plug decision 7's mapping or
  decision 8's `COMMENT ON` into. Decision 14 defines the additive layout
  this RFC introduces for it;
- **`schema`** (`schema_export.py`) — for a type with a specification, its
  schema file becomes the canonical source for that type's exported JSON
  Schema, instead of one derived purely from inferred/cast concept data.
  Decision 15 defines precedence against the command's existing
  `--infer-types`/`--cast` flags and where the resulting diagnostics are
  reported, for both the CLI and the MCP `schema` tool.

`check`, `inventory`, and `graph` are unaffected beyond decision 2's
discovery/reservation and decision 6's advisory diagnostics — none of them
materialize or export a schema, so decisions 7 and 8 have nothing to plug
into for them.

### 14. Persistent per-type materialization in `duckdb`: managed views, not physical copies

`attach_okf`'s existing four-table contract is **unchanged**:
`{schema}.concepts`, `{schema}.links`, `{schema}.reserved`,
`{schema}.diagnostics` (`schema` defaulting to `"okf"`) keep their current
shape, names, and `overwrite`/collision behavior exactly as today. These
four stay physical tables. Every bundle still gets them whether or not any
type has a specification.

Additively, for every concept type that has a specification, `attach_okf`
materializes one **persistent view**, not a physical table, into a
**second, dedicated schema**, `{schema}_types` (`"okf_types"` by default)
— never into `schema` itself — named for the exact type string, quoted,
the same convention `apply` already uses for its ephemeral tables. The
view selects and casts directly out of `{schema}.concepts`:

```sql
CREATE VIEW "okf_types"."Rotina" AS
SELECT
    concept_id AS __okf_concept_id,
    path AS __okf_path,
    json_extract_string(frontmatter_json, '$.title') AS title,
    TRY_CAST(json_extract_string(frontmatter_json, '$.tentativas') AS BIGINT)
        AS tentativas
FROM "okf"."concepts"
WHERE concept_type = 'Rotina';
```

Column types follow decision 7's physical type mapping; `COMMENT ON VIEW`/
`COMMENT ON COLUMN` carry decision 8's metadata — DuckDB persists and
recovers comments on a view exactly like a table, confirmed against the
`duckdb>=1.4,<2` range this project already depends on: created, closed,
reopened, and re-queried, both the view's column types (`DESCRIBE`) and
its `COMMENT ON VIEW`/`COMMENT ON COLUMN` text (`duckdb_views()`/
`duckdb_columns()`) come back unchanged. A type with **no** specification
gets no per-type view; its concepts remain reachable only through the
generic `concepts` table, as today.

**Why a view instead of a physical copy, and what a view does and does not
solve on its own.** A view is stored SQL text, re-executed on every query
— confirmed by the same prototype: replacing `{schema}.concepts` outright
(drop and recreate with different rows) left the existing view intact and
immediately reflecting the new data, no refresh step, no stale copy. That
genuinely removes data duplication, the "did this table refresh" question,
and the cost of re-copying every row on each `attach_okf` call. It does
**not**, by itself, resolve *ownership* — a `CREATE OR REPLACE VIEW`
replaces only the view of that exact name; a view for a type whose
specification later disappears does not get removed just because it's a
view rather than a table. Decision 14 still needs an explicit lifecycle
rule, the same way it did for physical tables — a view changes what that
rule costs, not whether one is needed.

**`{schema}_types` is a namespace `attach_okf` fully owns, not a shared
one it partially manages.** Given that ownership still has to be decided
either way, this RFC picks the simpler of the two real options: `{schema}_types`
holds nothing but this export's generated views, and every `attach_okf`
call resets it wholesale rather than reconciling it view by view. This is
consistent with what `{schema}_types` already was in this RFC's first
revision — a namespace reserved specifically so a per-type name could
never collide with `concepts`/`links`/`reserved`/`diagnostics` — extended
one step further: the whole namespace is generated, not just
collision-free.

- `overwrite=False`: if `{schema}_types` already exists and holds anything
  at all, `attach_okf` raises `BundleExportError` (extended over the new
  schema the same way it already covers `{schema}`'s four tables) rather
  than silently reusing or adding to it;
- `overwrite=True`: `attach_okf` runs `DROP SCHEMA "{schema}_types" CASCADE`
  followed by `CREATE SCHEMA "{schema}_types"`, then creates one view per
  specification-having type found by this run's discovery (decision 2) —
  confirmed against `duckdb>=1.4,<2` to cleanly remove every view in the
  schema and leave it ready for immediate recreation. A type whose
  specification was removed since the last run simply has no view created
  this time — there is nothing left to clean up separately, because
  nothing survives the drop;
- a caller who puts their own objects in `{schema}_types` is depending on
  behavior this RFC explicitly does not support: `{schema}_types` is
  reserved, generated, `attach_okf`-owned namespace, the persistent-`duckdb`
  counterpart to `.okf/specs/**`-adjacent reservation elsewhere in this
  RFC, not a shared workspace `attach_okf` partially respects. This is a
  narrower, and simpler, contract than physical per-type tables would have
  needed — no partial-ownership bookkeeping, no distinguishing "this
  export's table" from "a table with the same name someone else made."

Snapshotting a type's data into a real, physical, independently queryable
table — for workloads where view-time `TRY_CAST` cost or `{schema}.concepts`
availability genuinely matters — is deliberately **out of scope for this
RFC**. It is a coherent later addition (an opt-in materialization mode
alongside the view default) that does not require revisiting anything
decided here; this RFC does not attempt to design it now.

### 15. `schema` (`schema_export.py`): declared schema wins, `--cast` narrows, diagnostics per format

**Precedence.** For a property a type's specification declares, the
specification's `type` (mapped through the same JSON Schema vocabulary
`schema_export.py` already emits, not decision 7's DuckDB mapping, which
does not apply here) wins outright — inference is not consulted for that
property regardless of `--infer-types`, and a `--cast` naming the same
property is **not** silently applied over it. A `--cast` that names a
property the specification *does* cover is a diagnostic ("cast conflicts
with declared schema property"), added to decision 9's list — the cast is
reported, not applied, and the declared type is still what gets exported.

**A specified type's JSON output never grows properties `--infer-types`
found in the data.** "The declared schema is canonical" and "infer
whatever the data adds" are not both true at once — a specification
declaring `additionalProperties: false` and a concept document holding an
undeclared `legado` field is exactly the case where blindly merging an
inferred `legado` property into the exported `properties` would silently
turn something the specification declared *closed* into something the
export makes *open*. So, for a type that has a specification:
`schema`'s JSON output is the declared schema, re-emitted per decision 5 —
`properties` is never augmented with fields `--infer-types` observed but
the specification didn't declare, regardless of the flag. If the
specification's own `additionalProperties`/`unevaluatedProperties`
keyword is present and literally `false` — the one case this decision
reads either keyword for, a narrow exception to decision 5's "not acted
on" list, not a general adoption of closed-world validation — a concept
document holding a field the specification doesn't declare is a new
diagnostic ("undeclared field present under a closed schema"), added to
decision 9's list, advisory like every other case there. When the
specification is open (`additionalProperties` absent or `true`, the
example in decision 4 included), an undeclared observed field is simply
not diagnosed — the schema's own declared openness already says that's
fine. `--cast` and `--infer-types` keep their current, entirely unchanged
behavior for types with **no** specification at all; this decision only
narrows what a *specified* type's output does.

**Diagnostic transport.** `schema`'s two output formats have different
existing shapes and this RFC does not unify them into one envelope:

- **`--schema-format json`** (`export_json_schema`) already returns a
  dict (`root`, `total_types`, `inferred_types`, `casts`, `schemas`). This
  RFC adds a `"diagnostics"` key to that same dict — the advisory
  diagnostics from decision 6 and this decision's cast-conflict case, in
  the same shape `check`'s `diagnostics` array already uses. Both the CLI
  command and the MCP `schema` tool return this envelope unchanged in
  shape, just with the new key present (empty list when there is nothing
  to report);
- **`--schema-format zod`** (`export_zod_schema`) returns, and continues to
  return, a **bare Zod source string** — its entire purpose is being
  redirectable straight to a `.ts` file (`okf-parser schema . --schema-format
  zod > types.ts`), which a wrapping envelope would break. This RFC does
  not add a second return shape to it. Spec-compile diagnostics for Zod
  mode are written to **stderr**, human-readable, one per line, leaving
  stdout exactly the Zod source. Their exit code is **0** — `schema` has no
  `--normative-spec` (that flag belongs to `check` alone, per decision 9;
  an earlier draft of this decision incorrectly said Zod-mode diagnostics
  escalated through it) and this RFC does not give `schema` a new
  escalation flag, so an advisory diagnostic staying advisory means it
  never changes the exit code, in either format — consistent with decision
  6's "never rejected" policy rather than a Zod-specific carve-out. The
  process exits non-zero only when the artifact genuinely cannot be
  produced at all — an existing `SchemaExportError` unrelated to
  specification *content* (a concept type name collision, an invalid
  `--cast` specification string, and the like already raised before this
  RFC). **A broken or ambiguous specification is never one of those
  cases** — decision 9's eligibility fallback means a malformed schema
  file degrades that one type to no-spec behavior, advisory diagnostic
  reported, artifact still produced; it is deliberately excluded from
  `SchemaExportError`, not an unstated exception to it. The MCP `schema`
  tool's Zod mode keeps returning the same plain string it does today — an
  MCP caller that needs
  diagnostics alongside a Zod export calls `schema` with
  `schema_format="json"` instead; this RFC does not add a second output
  channel to the Zod MCP tool.

**`build_pydantic_models()`.** Unlike JSON and Zod, Pydantic is a library
API only — `schema_format` (CLI and MCP) is `Literal["json", "zod"]` and
never exposes it; `build_pydantic_models()` is called directly by Python
code that imports `okf_parser`. Its return type,
`dict[str, type[BaseModel]]`, is unchanged by this RFC — no diagnostics
envelope is added to it, for the same reason Zod's stdout contract stays a
bare string: wrapping a stable, narrow, already-used return type is a
bigger cost than the benefit. A caller who needs the diagnostics for a
bundle also compiled to Pydantic models calls `export_json_schema`/
`schema_bundle(fmt="json")` against the same bundle, which now carries them
(this decision's JSON-mode addition) — the same "call JSON mode for
diagnostics" answer this decision already gives for Zod, extended to
Pydantic rather than inventing a third mechanism.

## Alternatives considered

### A bundle-invented `fields: {name: {type, nullable, description}}` shape (first draft of this RFC)

Rejected. It is a schema language with exactly one implementation and no
tooling of its own, duplicating most of what JSON Schema already
standardizes — properties, required, nullability via union types, nested
structure, descriptions, `$ref`/composition for later reuse. Adopting JSON
Schema instead means `schema_export.py` can eventually treat a declared
schema as a source rather than something it only ever produces.

### Embedding the JSON Schema inline in the specification's YAML frontmatter

Considered, and initially preferred over a linked file. Rejected in favor
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
conventional filename. The flat, linked-file form (decision 1) requires no
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
- Which JSON Schema meta-schema validation library `okf-parser` adopts for
  decision 4's new meta-schema-conformance requirement — a real dependency
  this RFC now commits to needing, even though the exact package isn't
  named here.
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
