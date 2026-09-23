---
type: RFC
title: Sparse OKF authoring and declared defaults
status: accepted
description: Keep authored concepts free of boilerplate by omitting unknown optional fields and resolving stable defaults from type declarations
---

# RFC 0022: Sparse OKF authoring and declared defaults

## Summary

OKF is a knowledge format, not a form. A concept should say what is known about
that concept, not repeat template text or mechanically copy values that are
already determined by its type.

This RFC makes sparse authoring an explicit contract:

1. `type` remains the only core-required concept field.
2. Unknown optional facts should be omitted. YAML null is accepted when a
   manual editor or generator keeps the key, but omission is preferred.
3. Stable defaults are declared once in the type's sibling `.schema.sql`
   using ordinary DuckDB `DEFAULT` syntax. Defaults are never inferred from
   repeated observations.
4. Parsing preserves authored frontmatter exactly. It does not write defaults
   back into Markdown.
5. Typed materialization resolves a declared default only when the authored raw
   value is absent or YAML-null. A present non-null value follows the normal
   raw + `TRY_CAST` path.
6. A present but invalid value does not fall back to the default: its typed
   value is `NULL`, while the raw value remains available.
7. Generated contracts do not invent conventional optional fields such as
   `title` or `description` when they were neither declared nor observed.

## Why

Boilerplate damages the signal-to-noise ratio of a knowledge base. It makes
diffs larger, encourages agents to fabricate placeholder content, obscures
which facts were actually authored, and makes a type-wide policy look like
hundreds of independent assertions.

A repeated value that is semantically guaranteed by a type is one fact and
should have one home. A fact that is not known is not improved by spelling it
`unknown`, `N/A`, `TODO`, or an empty string in every document.

Sparse source also makes provenance clearer. The raw Markdown answers “what did
the author assert?” while the typed projection can answer “what is the effective
value under this type's declared defaults?”

## Authoring rule

A minimal concept is valid:

```markdown
---
type: Paper
---

The body can contain the knowledge that belongs in prose.
```

An optional unknown field should normally not appear:

```markdown
---
type: Paper
title: A title we actually know
---
```

Do not pad the same document merely because a template has more slots:

```markdown
---
type: Paper
title: A title we actually know
description: TODO
status: unknown
language: ""
---
```

The last example is valid YAML but poor OKF authoring unless those strings are
the actual domain values.

## Declared defaults

A type may move a stable repeated value into its existing DuckDB declaration:

```sql
CREATE TABLE "Paper" (
    status VARCHAR DEFAULT 'draft',
    language VARCHAR DEFAULT 'en'
);
```

A Paper that omits both fields stays sparse. In the typed relation, `status`
is `draft` and `language` is `en`.

The raw carrier columns remain `NULL`, which is intentional: absence in the
authored source and an effective default are different facts.

An explicit non-null authored value overrides the default. If that value cannot
be cast to the declared physical type, the typed value is `NULL`; the system
must not hide data drift by substituting the default.

YAML null is treated like absence for a column with a declared default. This
supports hand-edited documents that leave `field:` in place while still
making omission the preferred authoring style.

## Defaults are never inferred

A parser must not decide that a value is a default merely because every current
document repeats it. Repetition may be accidental, the sample may be incomplete,
or the value may change independently later.

Promotion to a default is an authored schema decision.

## Schema export

Observation-derived schema must not create fields simply because they are
popular conventions. In particular, `title` and `description` are ordinary
optional fields, not invisible requirements.

The export still includes:

- `type`, always;
- columns explicitly declared by the type schema, even before observation;
- fields actually observed in at least one document.

Presence remains observational. A declared default does not turn a field into
an authored required field.

## Agent guidance

An agent creating or editing OKF should optimize for information density:

- write the smallest frontmatter that truthfully represents the concept;
- omit unknown optional fields instead of guessing;
- never add placeholder prose merely to make a card look complete;
- move genuinely stable repeated values into a declared default;
- preserve explicit authored exceptions locally.

A scaffold therefore creates only the structural minimum needed to identify
the document. The author adds semantics, not filler.

## Compatibility

Existing concepts continue to parse unchanged. Documents with omitted optional
fields were already valid under the core parser; this RFC makes that property
explicit and removes schema-generation pressure to add conventional fields.

Existing `.schema.sql` declarations without `DEFAULT` behave exactly as
before. A declaration that already contains a DuckDB default now gives that
default effect in typed materialization, superseding RFC 0006's former rule
that defaults were ignored.
