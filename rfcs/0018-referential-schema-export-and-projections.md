---
type: RFC
title: Referential schema export and declared projections
status: proposed
description: Make schema export aware of the bundle relational contract, so generated Zod/Pydantic/JSON Schema reference sibling types instead of inlining them, and add declared projections as composed contracts
---

# RFC 0018: Referential schema export and declared projections

## Summary

`schema --format zod|pydantic|json` compiles one independent `TypeContract`
per concept type. Every contract is a closed tree of scalars, lists and
objects: there is no node that means "this field is a `Regra`". RFC 0007
already gave the bundle a place to say exactly that — `okf.schema.sql`
declares `Fundamentacao.regra REFERENCES "Regra"(nome)` — but the export
layer never reads it.

The consequence appears as soon as a consumer needs a composed shape. A
frontend or an MCP tool that returns "a `Processo` with its participants,
events and documents" has three options today, and all three are bad:

1. compose the pieces by hand in TypeScript and again in Python, which
   recreates the duplication the generated bindings exist to remove;
2. author a separate concept type whose documents inline the whole
   composition, which duplicates the referenced types' structure inside a
   second contract that can drift silently from the first;
3. give up on generation for composed shapes.

This RFC adds two capabilities:

- **referential export**: when the bundle declares a foreign key, the
  generated schema references the target type's schema instead of inlining
  or flattening it;
- **declared projections**: an optional bundle document that names a root
  type and the declared relations to traverse, compiled into a contract
  graph and exported through the same formats.

Both are opt-in. A bundle with no `okf.schema.sql` and no projection
documents exports exactly what it exports today.

## Motivation

The immediate consumer is CausaGanha (RFC 0015 there), which authors
`Processo`, `Publicacao`, `EventoProcessual`, `Documento`, `Pessoa`,
`InscricaoOAB` and `OrgaoJudicial` in a bundle and requires that its Web
(Zod/TypeScript) and its MCP server (Pydantic) never re-describe those
concepts by hand. Atomic types are already served well by
`export_zod_schema` and `export_pydantic_source`. The composed shapes its
product surfaces actually return — `processo_consultar`,
`publicacoes_buscar` — are not, and composing them per runtime is precisely
the drift the generated bindings were adopted to prevent.

The motivating need is general: any normalized 1:N:N bundle whose consumers
read an aggregate rather than a single row hits it.

## Decision

### 1. The relational contract is the composition source

Composition is read from `okf.schema.sql`, which already exists and is
already validated by `validate_relations`. This RFC introduces no second
place to declare that two types are related, and no naming or folder
convention is consulted.

`schema` gains `--relational-schema PATH`, mirroring `check`. Without it,
export is unchanged. With it, a field participating in a declared
`FOREIGN KEY` compiles to a reference node rather than to the scalar it
carries.

### 2. `RefNode`

`schema_contract` gains a fourth node:

```python
@dataclass(frozen=True)
class RefNode:
    """A field whose value identifies a document of another concept type."""
    concept_type: str
    key_columns: tuple[str, ...]
```

Rendering per format:

- **Zod** — emits the sibling variable, `ProcessoSchema`, and orders
  declarations by dependency. A cycle is rendered with
  `z.lazy(() => XSchema)` and a declared type annotation.
- **Pydantic** — emits the sibling model, with `from __future__ import
  annotations` and `model_rebuild()` for cycles.
- **JSON Schema** — emits `{"$ref": "#/$defs/Processo"}` and moves the
  per-type schemas under `$defs`.

Reference rendering never re-inlines the target's fields, so a change to
`Processo` reaches every schema that references it in the same regeneration.

### 3. Depth, and what a reference means at export time

A reference is a *type-level* fact, not a promise that the consumer holds
the referenced document. Export therefore has two modes, chosen per run:

- `--refs=key` (default): the field keeps its scalar key type and carries
  `x-okf-references` metadata. This is what a row-shaped consumer wants.
- `--refs=embed`: the field becomes the referenced contract.

The mode is per run, not per field. A projection already declares which
members come composed and which stay keys, so a per-field CLI selector
would be a second place to say the same thing — the thing this RFC exists
to prevent.

Per-run embedding alone, however, is all-or-nothing, and a domain graph is
usually cyclic: in the motivating bundle `Pessoa -> Processo -> Publicacao
-> Processo` closes a loop, so a global embed renders nearly every node
through `z.lazy` and every Pydantic model through `model_rebuild()`.
Correct, unreadable.

The missing control is therefore depth, not per-field granularity:

- `--depth=N` bounds how many reference hops `--refs=embed` follows.
  Beyond the bound, a reference degrades to its `--refs=key` form rather
  than being dropped, so the shape stays honest about what it omitted.
- `--depth=1`, the default when `--refs=embed` is given, embeds direct
  children only. It answers the real request ("a `Processo` with its
  publications") and makes cycles unreachable by construction instead of
  survivable through `lazy`.
- `--depth=0` is `--refs=key`. An unbounded depth must be asked for
  explicitly (`--depth=max`), and only then do the cycle-rendering rules
  in section 2 apply.

### 3.1. Composite foreign keys

`ForeignKeyConstraint` already carries `columns` and `referenced_columns`
as ordered tuples, so the relational contract has described composite keys
since RFC 0007. The identity model of the motivating consumer is composite
by design — `(fonte, tipo, source_id)` namespaces every record read from a
source — so `EventoProcessual -> Publicacao` is a composite reference in
the first bundle that will use this feature. Excluding composite keys would
ship a mechanism that fails on its own first case.

The two modes are not symmetric, though:

- **`--refs=key` supports composite keys.** Each participating column
  carries `x-okf-references` naming the whole constraint and the column's
  position in it. Nothing has to be renamed, because no field disappears.
- **`--refs=embed`, run standalone, supports single-column keys only.**
  Embedding replaces *N* fields with *one* object-valued member, and
  outside a projection there is no name for it: `processo_fonte`,
  `processo_tipo` and `processo_source_id` do not compose into an obvious
  member name, and inventing one (longest common prefix, target type name)
  would put a naming convention in the parser — which section 1 forbids.
  A composite key under standalone `--refs=embed` is a normative error
  naming the constraint and saying that embedding it requires a projection.
- **Projections embed composite keys.** The member's `as` is the name, so
  the ambiguity disappears at the point where an author already had to
  choose one.

This is a declared limit, not a silent gap: the error points at the
mechanism that does support the case.

### 5. Declared projections

A projection is an OKF document with `type: Projection` in a bundle:

```yaml
---
type: Projection
name: ProcessoConsultar
root: Processo
include:
  - relation: Publicacao.processo
    as: publicacoes
  - relation: EventoProcessual.processo
    as: eventos
  - relation: Pessoa.processo
    as: participantes
    optional: true
---
```

Each `relation` names a foreign key that `okf.schema.sql` already declares,
written `FromType.field`. The parser resolves its direction: a foreign key
pointing at the root yields a list on the root (1:N seen from the target,
per RFC 0007's rule that N:1 is the primitive); a foreign key on the root
yields a single embedded value. An `include` naming a relation the
relational contract does not declare is a normative error — a projection
cannot invent a relationship.

`optional: true` makes the member nullable; the default is required. No
other member-level semantics are recognized in v1: pagination, envelopes and
transport shapes belong to the consumer, not to the contract.

The compiled result is one `TypeContract` per projection, exported through
the same `--format` values, with the same reference rules. Projections do
not become concept types: they have no documents, they are never
materialized as typed relations, and they do not participate in `apply`.

### 6. Emit type aliases in the Zod renderer

`render_zod` gains `--emit-types`, adding one line per contract:

```ts
export const ProcessoSchema = z.object({ /* ... */ });
export type Processo = z.infer<typeof ProcessoSchema>;
```

This is small and it removes a whole category of hand-written file. Today
every TypeScript consumer must maintain a barrel whose only job is to turn
generated schemas into named types — a file that is manual by construction
and therefore a place where a domain declaration can be smuggled back in.
With the alias emitted, the generated file is the complete public surface
and the barrel can be deleted rather than policed.

## Non-goals

- No inference of relationships from names, paths or folder layout.
- No query language. A projection declares composition, not filters,
  ordering or pagination.
- No persistence: projections are not tables and are not written back.
- No change to `apply`, `import`, or the typed-relation materialization.
- No new trust boundary. `okf.schema.sql` keeps exactly the trust it has
  under RFC 0007, and projection documents are ordinary bundle Markdown.

## Compatibility

Every addition is opt-in and default-off:

| Surface | Without new flags | With them |
| --- | --- | --- |
| `schema --format zod` | unchanged | references, optional type aliases |
| `schema --format pydantic` | unchanged | references |
| `schema --format json` | unchanged | `$defs` + `$ref` |
| bundles with no `Projection` document | unchanged | — |

Output stays deterministic: contracts are ordered by dependency and then by
type name, so regeneration in CI keeps producing an empty diff.

## Rollout

Stacked, each step independently reviewable and releasable:

1. this RFC;
2. `RefNode` + `--relational-schema` + `--refs=key` metadata across the
   three formats;
3. `--refs=embed` with `--depth`, the composite-key error, and the
   cycle-rendering corpus that `--depth=max` needs;
4. `Projection` documents: parsing, resolution against the relational
   contract, and normative errors;
5. projection export across the three formats;
6. `--emit-types` for Zod;
7. CLI, MCP `schema` tool, `docs/cli.md` and changelog.

## Open questions

1. Should a projection be allowed to exclude fields of its root type?
   Excluding is how a projection stays small, but it also lets a projection
   disagree with the type it projects. v1 says no; the composed shape is the
   root type plus declared members.
2. Is `--depth=max` worth shipping in v1 at all? It is the only mode that
   needs cycle rendering, and a projection can always state the shape it
   wants explicitly.

Two questions from the first draft are now answered in sections 3 and 3.1:
`--refs=embed` is per run, with `--depth` rather than per-field selection,
and composite keys are supported by `--refs=key` and by projections, but
rejected with a named error under standalone `--refs=embed`.
