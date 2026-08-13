---
type: RFC
title: Bundle relational schema
status: accepted
description: Add one optional bundle-level okf.schema.sql contract for UNIQUE, PRIMARY KEY, and FOREIGN KEY relationships across concept types.
---

# RFC 0007: Bundle relational schema

## Summary

RFC 0006 keeps each concept type's physical field declaration beside that type's specification document as a derived `.schema.sql`. Cross-type integrity does not fit that scope: a `Fundamentacao.schema.sql` cannot declare `REFERENCES "Regra"(nome)` without also creating a second, partial `Regra` table inside the same DuckDB connection.

This RFC therefore adds one **bundle-level relational contract**, `okf.schema.sql`, whose job is only to describe keys and relationships that span the bundle. Per-type `.schema.sql` files remain authoritative for field types. `okf.schema.sql` is authoritative for relational identity.

For the motivating model:

```sql
CREATE TABLE "Regra" (
  nome VARCHAR UNIQUE
);

CREATE TABLE "Fundamentacao" (
  id VARCHAR PRIMARY KEY,
  regra VARCHAR REFERENCES "Regra"(nome)
);
```

Only columns participating in keys or references need to appear in this file. Other frontmatter fields, such as `requisitos`, remain solely in the type's normal schema/documentation.

The resulting bundle semantics are ordinary relational semantics: one `Regra.nome` can be referenced by zero or many `Fundamentacao.regra` rows; duplicate rule names and dangling references are invalid.

## Decision

1. The optional contract lives at the fixed bundle-root path `okf.schema.sql`. No frontmatter field points to it, so there is no second path fact that can disagree.
2. The file is trusted DuckDB SQL, executed only when the caller explicitly opts into relational validation. It carries the same trust boundary as RFC 0006 `.schema.sql` files.
3. The parser hands the file to DuckDB whole and reads table/constraint metadata back from `duckdb_tables()`, `duckdb_columns()`, and `duckdb_constraints()`. It does not parse SQL text itself.
4. v1 recognizes `PRIMARY KEY`, `UNIQUE`, and `FOREIGN KEY`. Composite keys preserve authored column order.
5. Only columns participating in recognized constraints matter to this contract. Their DuckDB types must be scalar; the initial implementation targets the natural-key use case and does not invent equality for nested YAML values.
6. A table name is the exact authored OKF `type` value. A column name is the exact frontmatter key under DuckDB identifier equality, matching the existing declared-schema machinery.
7. `PRIMARY KEY` requires every local key to be present and unique. `UNIQUE` ignores rows where any key component is null and requires all remaining tuples to be unique. `FOREIGN KEY` ignores rows where any local component is null and otherwise requires an equal referenced tuple.
8. Validation runs against the bundle's concept rows, not against data inserted into the declaration database. The declaration database is metadata-only; this avoids DuckDB load-order constraints and keeps diagnostics tied to the original Markdown paths.
9. A referenced table/column must exist in the same `okf.schema.sql`; DuckDB itself establishes that during DDL execution. A concept type may have zero documents; it is still a valid target with an empty key set.
10. Relational violations are deterministic normative errors carrying the declaring type, constraint columns, offending value tuple, and source Markdown path.

## API

The first public surface is explicit:

```python
bundle = load_bundle(root)
violations = validate_relations(bundle, root / "okf.schema.sql")
```

and CLI validation gains an opt-in flag equivalent to:

```bash
okf-parser check ./bundle --relational-schema okf.schema.sql
```

A later RFC may make a bundle-root declaration automatically normative, but v1 keeps execution opt-in because SQL files are trusted code.

## Initial consumer

The first consumer is a legal-rule bundle with two concept types:

- `Regra`: `nome` is the stable unique natural key;
- `Fundamentacao`: each record has its own identity and a `regra` foreign key referencing `Regra.nome`.

That gives `Regra 1:N Fundamentacao` without embedding fundamentations in the rule document or duplicating the rule itself.
