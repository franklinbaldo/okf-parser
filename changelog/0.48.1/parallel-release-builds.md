---
type: Release Note
title: Parallel source consumer and compatible Cargo caching
---

- Run the cold, bundled-DuckDB source consumer in parallel with all five platform wheel builds, retaining it as a required release-set gate.
- Cache compatible native Cargo dependencies for CI, macOS Intel/ARM and Windows, with separate compiler, platform, feature and DuckDB identities. Always rebuild and exercise each wheel.
- Reuse an absolute Cargo target across CI source packaging and installation to avoid compiling the same dependencies twice. Keep release optimization settings and all validations unchanged.
