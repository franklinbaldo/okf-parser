---
type: Documentation
title: Rust build performance
description: Measure Rust build costs and use the engine-only development path
---

# Build performance

The Rust workspace is intentionally split into `okf-engine`, `okf-db`, and
`okf-core`. Development feedback should use the narrowest crate that matches
the change instead of paying for SQL/MCP dependencies unconditionally.

## Fast engine loop

For parsing, validation, graph, search, formatting, or writing changes:

```bash
cargo fmt --all --manifest-path Cargo.toml --check
cargo clippy --locked -p okf-engine --all-targets --manifest-path Cargo.toml -- -D warnings
python scripts/cargo_timings.py \
  --label engine-tests \
  --output build-metrics/engine-tests.json \
  -- cargo test --locked -p okf-engine --manifest-path Cargo.toml --timings
```

This path does not fetch DuckDB and does not compile `okf-db`, `okf-core`,
RMCP, Axum, or Tokio merely to test the semantic engine.

## Full Rust gate

The existing `Rust semantic engine` workflow remains authoritative for the
engine + SQL + CLI/MCP stack. It is instrumented with the same wrapper so its
wall times can be compared to the engine-only job.

Cargo's HTML timing reports live under `$CARGO_TARGET_DIR/cargo-timings`;
the wrapper also writes a small JSON record with elapsed wall time, commit,
runner, command, tool version, and the timing files that existed at completion.

The timing data is observational, not a performance gate. Do not weaken the
clean source-distribution install test or change release optimization settings
based on a single run.


## Capability feature graph

The public `okf-parser` binary requires the complete `full` capability set.
That keeps release behavior unchanged while allowing narrower development
dependency graphs:

```bash
# Engine facade only: no DuckDB and no MCP/HTTP stack.
cargo check -p okf-core --no-default-features --lib

# SQL dependencies without the MCP/HTTP surface.
cargo check -p okf-core --no-default-features --features sql --lib

# MCP/HTTP dependencies without DuckDB.
cargo check -p okf-core --no-default-features --features mcp --lib

# Complete public binary, linking an external/prebuilt DuckDB.
cargo build -p okf-core --no-default-features --features full

# Complete public binary with bundled DuckDB (the normal default).
cargo build -p okf-core
```

Official dynamic wheel/CI builds therefore use
`--no-default-features --features full`. A bare
`--no-default-features` is intentionally the lightweight engine-development
graph and does not build the public binary.


## Cold source-distribution consumer

The release dry run intentionally keeps one expensive proof that cannot reuse a
native wheel, Cargo target, or prebuilt DuckDB: install the exact sdist into a
fresh environment with bundled DuckDB. That job now passes Maturin
`--timings=html` and wraps the installation with `scripts/cargo_timings.py`.
The resulting JSON and Cargo HTML are uploaded as `sdist-cold-timings`.

This instrumentation does not make the source build warm or cached. It exists
to identify which crates and native build units dominate the cold path before
changing source-distribution policy or build architecture.


## Paired DuckDB source-build experiment

The optional `Cold sdist DuckDB A/B` workflow compares two source builds on
the same hosted runner after a shared `cargo fetch`:

- the canonical sdist build with bundled DuckDB;
- the same sdist with `--no-default-features --features full` linked against
  the repository's pinned prebuilt libduckdb.

Each arm uses an independent empty Cargo target and Python environment. Python
dependencies are installed before timing and the package is installed with
`--no-deps`, so the measured delta is primarily native build cost rather than
registry or Python dependency download time.

This is an experiment only. The external-libduckdb arm depends on the fetched
library remaining available through the job environment and is not a proposed
replacement for the self-contained source-distribution contract.
