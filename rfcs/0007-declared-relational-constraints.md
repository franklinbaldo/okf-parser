---
type: RFC
title: Declared relational constraints
status: accepted
description: Preserve UNIQUE, PRIMARY KEY, and FOREIGN KEY constraints from trusted per-type .schema.sql declarations and validate them against bundle rows.
---

# RFC 0007: Declared relational constraints

## Summary

RFC 0006 already makes a type's derived `.schema.sql` file the trusted declaration for its physical columns. This RFC extends that same declaration to relational integrity: `PRIMARY KEY`, `UNIQUE`, and `FOREIGN KEY` constraints are part of the type contract rather than disposable DDL details.

A producer may therefore describe, for example:

```sql
CREATE TABLE "Regra" (
  nome VARCHAR UNIQUE
);
```

and:

```sql
CREATE TABLE "Fundamentacao" (
  id VARCHAR PRIMARY KEY,
  regra VARCHAR REFERENCES "Regra"(nome),
  requisitos VARCHAR[]
);
```

The bundle-level relation is then ordinary relational semantics: one `Regra.nome` may be referenced by zero or many `Fundamentacao.regra` rows, while duplicate rule names and dangling references are invalid.

## Decision

1. `parse_declared_schema()` reads `PRIMARY KEY`, `UNIQUE`, and `FOREIGN KEY` metadata from DuckDB's own `duckdb_constraints()` catalog after executing the trusted schema script. It does not parse SQL text itself.
2. Constraint identity preserves authored table and column spelling as returned by DuckDB. Composite keys are represented as ordered tuples.
3. Declared constraints are exposed in `DeclaredSchema` and carried into typed-table plans.
4. Bundle validation happens after all declared type tables are populated, so foreign keys can refer across concept types regardless of declaration/materialization order.
5. `PRIMARY KEY` means non-null uniqueness. `UNIQUE` means uniqueness among non-null values, matching DuckDB semantics. `FOREIGN KEY` ignores rows whose local key contains `NULL` and otherwise requires a matching referenced key.
6. A foreign key may reference another declared concept type only. A referenced table absent from the bundle's declared schemas is a contract error, not an inferred relation.
7. The parser reports relational violations with the declaring type, constraint, columns, and offending values; it does not silently drop or coerce rows.

## Why this belongs in `.schema.sql`

No second OKF-specific relation syntax is introduced. The schema declaration is already executable trusted DuckDB SQL, and DuckDB already has the exact vocabulary required for keys and references. Reusing it avoids parallel facts that can disagree and keeps the relational contract queryable by ordinary SQL tooling.

## Initial consumer

The motivating shape is a legal-rule bundle with two concept types:

- `Regra`: `nome` is unique and is the stable natural key;
- `Fundamentacao`: each row has its own identity and a `regra` foreign key referencing `Regra.nome`.

This encodes `Regra 1:N Fundamentacao` without embedding fundamentations inside the rule document or duplicating rule facts.
