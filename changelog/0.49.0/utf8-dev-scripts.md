---
type: Release Note
title: Development scripts read subprocess output as UTF-8
---

- `benchmarks/capability_matrix.py`, `scripts/cargo_timings.py`, `scripts/check_feature_graph.py` and `scripts/upstream_conformance.py` decode subprocess output explicitly as UTF-8, so they no longer depend on the Windows console code page (#305).
