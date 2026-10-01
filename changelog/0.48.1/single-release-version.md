---
type: Release Note
title: One authored release version
---

- Keep the release version in the root Cargo workspace. Rust crates inherit it and Maturin derives Python package metadata from it.
- Generate npm versions, local dependency constraints, lockfile snapshots, TypeScript release metadata and README references with one offline, idempotent command.
- Reject stale generated metadata in CI and release workflows while preserving independent wire-protocol versions and the unpublished DuckDB extension version.
