---
type: Release Note
title: Sparse authoring and declared defaults
---

- make sparse authoring explicit: `type` is the only core-required concept field and unknown optional fields should be omitted rather than padded with placeholders;
- stop schema export from synthesizing unobserved `title` and `description` fields;
- retain DuckDB column defaults from declared `.schema.sql` files and apply them to omitted or YAML-null values during typed materialization;
- keep present invalid values visible as typed `NULL` instead of masking them with a default;
- make generated type-spec stubs structurally minimal, with no `TODO` boilerplate.
