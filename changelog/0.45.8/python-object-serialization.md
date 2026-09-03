---
type: Release Note
title: Python objects can project themselves into typed OKF representations
---

- Add `OKFRepresentation`, `OKFDocument`, the structural `SupportsOKF`/`__okf__` protocol, `to_okf()` conversion, and `dumps()` canonical rendering.
- Infer the required OKF `type` from a producer class by default while allowing metadata or caller overrides; require an explicit type for bare mappings rather than emitting `type: dict`.
- Support JSON-like mappings directly and Pydantic models through `model_dump(mode="json")`; custom `__okf__()` implementations choose which data lives in YAML metadata and which lives in the optional body.
- Normalize JSON scalars into OKF's spelling-preserving scalar domain while keeping YAML syntax, canonical physical ordering, and authored-document edit preservation inside `okf-parser`.
