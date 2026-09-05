---
type: Release Note
title: Bundle relation SQL executes over materialized type tables
---

- Add an explicit `compile_bundle_types(..., relations=True)` slice that executes optional trusted `okf.relations.sql` only after RFC 0006 has materialized declared types into `okf_types` in the same DuckDB connection.
- Reserve `okf_relations` as the producer output namespace, inventory its published views/tables deterministically, and expose them through `TypedRelations.bundle_relation()`.
- Keep relation execution opt-in, treat an absent relation program as a valid empty catalog, and surface malformed trusted SQL through a dedicated `BundleRelationsError`.
- Add regression coverage proving a producer relation can join two real typed tables without redeclaring either table.
