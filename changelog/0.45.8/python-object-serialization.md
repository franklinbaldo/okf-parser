---
type: Release Note
title: Python objects can project themselves into typed OKF representations
---

- Add `OKFRepresentation`, `OKFDocument`, the structural `SupportsOKF`/`__okf__` protocol, `to_okf()` conversion, and `dumps()` canonical rendering.
- Infer the required OKF `type` from a producer class by default while allowing valid metadata or caller overrides; require an explicit type for bare mappings rather than emitting `type: dict`.
- Support JSON-like mappings directly and mapping-shaped Pydantic models through `model_dump(mode="json")`; root/scalar Pydantic models opt in explicitly with `__okf__()` instead of being wrapped in an invented metadata field.
- Resolve an explicit `__okf__` hook once and invoke it once; reject non-callable explicit hooks rather than silently falling back to another projection.
- Normalize JSON scalars into OKF's spelling-preserving scalar domain while keeping YAML syntax, canonical physical ordering, and authored-document edit preservation inside `okf-parser`.
