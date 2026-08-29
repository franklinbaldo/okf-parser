---
type: Release Note
title: impact conformance benchmark baseline
---

- Preserve a reproducible microbenchmark for the executable `impact` conformance kernel, separating parse/validation, canonical sort, serialization and digest cost across 1–50k records. The benchmark records runner variance and establishes the rule that future accelerators must prove canonical byte equality first and compare speed within the same run rather than against absolute CI latency thresholds.
