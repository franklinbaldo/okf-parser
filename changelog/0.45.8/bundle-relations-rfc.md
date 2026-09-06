---
type: Release Note
title: Bundle relations can build on real typed tables
---

- Propose RFC 0021: per-type `.schema.sql` files continue to define each OKF type independently, while one optional bundle-root `okf.relations.sql` executes afterward over the real materialized `okf_types` tables.
- Reserve `okf_relations` for producer-defined cross-type query surfaces and propose a canonical `edges` projection for generic navigation without flattening every relational query into graph semantics.
- Preserve unresolved references as queryable relation data and separate relation observations from integrity diagnostics.
- Mark RFC 0007's metadata-only partial-table redeclaration architecture as superseded while retaining it as a compatibility/migration concern.
- Reconcile RFC 0012 as a consumer layer: agent-facing query/lookup/graph/impact surfaces may consume canonical relation outputs, but must not become a second authority for cross-type relational facts.
