---
type: RFC
title: Bundle relation SQL over materialized type tables
status: proposed
description: Execute one trusted bundle-level DuckDB SQL contract after per-type schemas materialize, making cross-type relations queryable without redeclaring type tables
---

# RFC 0021: Bundle relation SQL over materialized type tables

## Summary

RFC 0006 gives each producer-defined OKF type an adjacent trusted DuckDB `.schema.sql`. Those declarations are compiled into real typed tables in one DuckDB connection under `okf_types`.

Cross-type relations should be declared **after that materialization**, in one bundle-level trusted SQL file that queries the real typed tables directly.

The contract is:

```text
specs/A.schema.sql + specs/B.schema.sql
        ↓
materialize real typed rows
        ↓
okf_types.A + okf_types.B
        ↓
okf.relations.sql
        ↓
okf_relations.*
```

This RFC supersedes RFC 0007's metadata-only architecture. RFC 0007 required `okf.schema.sql` to recreate partial empty tables only so DuckDB could expose PK/UNIQUE/FK metadata, after which Python reimplemented those constraints against frontmatter. RFC 0021 removes that duplication: the bundle relation program runs against the actual typed data and DuckDB itself remains the relational language/execution engine.

The design principle is deliberately small:

> `.schema.sql` describes the nodes; `okf.relations.sql` describes relationships among those nodes.

## Motivation

A bundle evolves type by type. Each type can be understood independently at first:

```text
Regra.schema.sql
Fundamentacao.schema.sql
Requisito.schema.sql
```

Later the producer discovers relationships:

```text
Fundamentacao.regra → Regra.nome
Requisito.fundamentacao → Fundamentacao.id
```

The same pattern appears in editorial corpora:

```text
article-ready.sources → source-observation
editorial-ficha-response → editorial-ficha
```

These relationships do not belong to either table in isolation. They belong to the bundle model.

The existing typed materializer already solves the hard prerequisite: all declared type rows are present together in one DuckDB connection, with canonical internal identity columns such as `__okf_concept_id` and `__okf_path` plus typed producer fields. A second relation DSL or partial-table declaration duplicates information DuckDB already has.

## Decision

### 1. Add one optional bundle-root `okf.relations.sql`

The fixed v1 path is:

```text
<bundle-root>/okf.relations.sql
```

No frontmatter field points to it. The path is derived by convention, avoiding a second authored path fact.

The file is optional globally. A bundle without it remains valid.

### 2. Relation SQL executes only after typed materialization

Execution order is normative:

```text
load OKF bundle
→ discover/parse per-type declarations
→ materialize declared types into okf_types
→ create reserved okf_relations schema
→ execute okf.relations.sql in the same connection
→ expose resulting relation projections
```

The SQL sees the real tables exactly as application queries see them.

Example:

```sql
CREATE VIEW okf_relations.fundamentacao_regra AS
SELECT
    f.__okf_concept_id AS source_id,
    r.__okf_concept_id AS target_id,
    f.regra AS referenced_key
FROM okf_types."Fundamentacao" AS f
LEFT JOIN okf_types."Regra" AS r
    ON r.nome = f.regra;
```

No `Regra` or `Fundamentacao` DDL is repeated.

### 3. Keep the RFC 0006 trust model

`okf.relations.sql` is trusted DuckDB SQL, with the same security boundary as `.schema.sql`: closer to a Makefile/migration than to JSON Schema.

The parser:

- does not parse SQL text into its own AST;
- does not implement a restricted SQL dialect;
- hands the file to DuckDB whole;
- executes it only through an explicit relation-enabled compile/export/check operation;
- documents that untrusted bundle SQL must not be executed.

Ordinary parsing/inventory does not silently execute trusted SQL.

### 4. Reserve `okf_types` as input and `okf_relations` as output

Producer relation SQL reads typed data from:

```text
okf_types.<type>
```

and publishes bundle-level relational artifacts under:

```text
okf_relations.*
```

The first implementation may allow trusted SQL to create auxiliary objects elsewhere because the trust model is intentionally not a sandbox, but only objects in `okf_relations` are part of the relation contract returned by the parser.

### 5. Define a canonical `okf_relations.edges` projection

When producer relation SQL wants its relations to participate in generic navigation/graph/application APIs, it publishes `okf_relations.edges` with this v1 shape:

```text
source_type  VARCHAR
source_id    VARCHAR
predicate    VARCHAR
target_type  VARCHAR NULL
target_id    VARCHAR NULL
target_ref   VARCHAR NULL
resolved     BOOLEAN
origin       VARCHAR
```

Semantics:

- `source_id` is the canonical concept id of the source row;
- `source_type` is its OKF type;
- `predicate` names the relation and remains producer-defined unless an OKF/core standard owns the predicate;
- `target_id` is the canonical target concept id when resolved;
- `target_type` is the resolved/expected target type when known;
- `target_ref` preserves the authored/producer reference used to locate a target when useful;
- `resolved` distinguishes a successful target lookup from a dangling reference;
- `origin` distinguishes relation provenance, initially including `producer-sql`; later standard OKF/frontmatter and Markdown-link projections may use distinct values.

A producer may publish any additional `okf_relations.*` views for domain-specific queries. `edges` is only the generic interoperability surface.

### 6. Do not require all relations to be graph edges

SQL is richer than a graph edge table. Producers may create:

```text
okf_relations.article_sources
okf_relations.rule_dependencies
okf_relations.open_obligations
okf_relations.consistency_summary
```

These remain ordinary DuckDB relations. Only facts intentionally projected into `okf_relations.edges` become generic graph/navigation edges.

This prevents the parser from flattening every relational query into an impoverished graph model.

### 7. Preserve unresolved relations as data

A dangling reference is often valuable evidence. Relation SQL should normally use `LEFT JOIN`/equivalent when the producer wants unresolved references represented.

For example:

```sql
SELECT
    f.__okf_concept_id AS source_id,
    'Regra' AS target_type,
    r.__okf_concept_id AS target_id,
    f.regra AS target_ref,
    r.__okf_concept_id IS NOT NULL AS resolved
FROM okf_types."Fundamentacao" AS f
LEFT JOIN okf_types."Regra" AS r ON r.nome = f.regra;
```

The parser must not discard the unresolved row merely because there is no target node.

### 8. Separate relation observations from integrity diagnostics

A relation row answers:

> What relation/reference does the bundle currently contain?

A violation answers:

> Which relational invariant does the current data fail?

These are different outputs.

RFC 0021 reserves a future canonical diagnostics projection under `okf_relations` (tracked separately) rather than requiring foreign-key load failures. This matters because an invalid reference should remain inspectable as data.

Producer SQL may express duplicate keys, missing references, aggregate inconsistencies and similar cross-type rules directly over the materialized tables. The parser owns the stable diagnostic envelope; it does not reimplement each predicate in Python.

### 9. Relation SQL may use structured DuckDB features

Because RFC 0006 typed tables may contain lists, JSON and scalar typed columns, relation SQL may naturally use:

- `JOIN` / `LEFT JOIN`;
- `UNNEST`;
- JSON functions;
- CTEs;
- macros;
- views;
- aggregate/window functions;
- DuckDB casts and operators.

This is an explicit advantage over a narrow PK/FK-only metadata contract.

### 10. Generic graph/navigation consumes canonical edges

The long-term relation authority becomes:

```text
canonical typed rows + relation SQL → canonical relation outputs
```

NetworkX remains a projection, not a store.

A generic graph API may later choose predicates/origins:

```python
relations.to_networkx(predicate="source")
```

or combine producer edges with parser-observed Markdown links. Markdown links remain distinguishable by `origin`; relation SQL does not erase syntax provenance.

### 11. Existing `resolve_relations()` becomes a convenience, not a second authority

Today `resolve_relations()` reads structured frontmatter lists while `Bundle.links`/NetworkX read Markdown body links. RFC 0021 establishes a convergence target: application/reverse-link/graph surfaces should consume canonical relation rows rather than requiring consumers to know which syntax generated a relation.

The migration can be incremental. RFC 0021 does not require the first PR to rewrite every relation consumer.

### 12. Supersede RFC 0007's metadata-only execution model

RFC 0007 remains historically useful but its architecture is superseded.

New bundles should not need to write:

```sql
CREATE TABLE "Regra" (...);
CREATE TABLE "Fundamentacao" (... REFERENCES "Regra"(...));
```

merely to redeclare columns that already exist in `.schema.sql`.

Compatibility for existing `okf.schema.sql` and `--relational-schema` callers is tracked separately. Compatibility is a migration surface, not a second permanent relation model.

### 13. `okf init` may scaffold but must not invent semantics

A future init improvement may create a starter `okf.relations.sql` containing:

- the reserved `okf_relations` output convention;
- an empty/canonical edges view template;
- comments/examples using observed type/table names.

It must not infer that `foo_id` or a path-looking string is a semantic relation merely from its spelling.

## Public API direction

The first vertical slice extends the existing typed compile path conceptually:

```python
with bundle.compile_types(
    spec_template="specs/{slug}.md",
    relations=True,
) as compiled:
    article = compiled["article-ready"]
    edges = compiled.relation("edges", namespace="okf_relations")
```

Exact spelling is implementation-level and may change during the first slice. The invariant is that type and relation queries share one live DuckDB connection and one materialized state.

Persistent DuckDB export should eventually support the same optional relation execution rather than compiling a different semantic model.

## Error model

Relation-enabled compilation fails explicitly when:

- `okf.relations.sql` cannot be read as UTF-8;
- DuckDB rejects the trusted SQL;
- the script violates reserved output/catalog postconditions;
- a required canonical relation such as `edges`, when explicitly requested, has the wrong schema.

Absence of `okf.relations.sql` is not an error unless the caller explicitly requires it.

Errors in producer data represented by relation rows are not automatically SQL execution failures. They belong in relation/diagnostic output.

## Testing

The implementation stack must cover at least:

1. two declared types materialized before relation SQL executes;
2. relation SQL joining real `okf_types` rows;
3. relation SQL cannot succeed by depending on metadata-only duplicate tables;
4. resolved and unresolved N:1 references;
5. canonical concept ids carried through relation output;
6. deterministic relation catalog/output ordering;
7. absent relation SQL;
8. malformed relation SQL;
9. explicit trusted-SQL execution boundary;
10. list/structured relation via `UNNEST` in a later conformance slice;
11. persistent/in-process projection equivalence where both support relations;
12. eventual graph/reverse-navigation consumption of canonical edges.

## Alternatives considered

### Extend RFC 0007 with more parsed constraint metadata

Rejected as the primary model. It keeps re-declaring partial type tables and asks Python to reimplement relational execution DuckDB already provides.

### Put foreign keys into each type's `.schema.sql`

Rejected. RFC 0006 deliberately executes each declaration independently to learn one type's shape; cross-type references do not belong to an isolated declaration connection.

### Add a YAML/JSON relation DSL

Rejected. It would be a second relational language with less expressive power than DuckDB SQL and would require parser-specific semantics for joins, lists, nulls and diagnostics.

### Treat Markdown links as the complete relation model

Rejected. Markdown links encode navigation/syntax, not every producer-defined semantic relation, and structured frontmatter relations already exist independently.

### Enforce real DuckDB FOREIGN KEY constraints on loaded data

Insufficient as the general model. It makes invalid references load failures, does not represent unresolved relations as queryable data, and cannot express the wider set of producer relationships/derived relations/aggregate checks needed by OKF consumers.

## Consequences

The architecture becomes:

```text
authored OKF
    ↓
per-type `.schema.sql`
    ↓
real typed tables (`okf_types`)
    ↓
bundle `okf.relations.sql`
    ↓
relation views/tables (`okf_relations`)
    ↓
Ibis / DuckDB / graph / application adapters
```

The core owns ordering, execution lifecycle, reserved namespaces and generic relation projection contracts. Producers own their domain relationships in SQL. DuckDB remains the relational engine instead of serving only as a parser for a duplicated metadata schema.

## Work breakdown

- #238 — execute `okf.relations.sql` after typed materialization;
- #239 — canonical `okf_relations.edges` contract;
- #240 — Python/Ibis/DuckDB public relation surfaces;
- #241 — graph/reverse navigation over canonical relations;
- #242 — relational integrity/violations via bundle SQL;
- #243 — RFC 0007 compatibility/migration;
- #244 — `okf init` relation scaffold;
- #245 — cross-runtime relation conformance corpus.

Parent tracking: #237.
