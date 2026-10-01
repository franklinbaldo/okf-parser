---
type: Documentation
title: Rust build profile experiments
description: Compare non-release Rust profiles for development without changing shipped optimization
---

# Rust build profile experiments

The production `[profile.release]` remains the artifact baseline: thin LTO,
one codegen unit, and stripping. The repository also carries two explicitly
experimental profiles so their trade-offs can be measured in CI without
changing shipped binaries.

A GitHub-hosted Ubuntu run built the complete dynamic-DuckDB product from
separate cold target directories and then benchmarked the same deterministic
1,000-document OKF corpus five times per command.

| profile | cold build | binary | inventory median | search median | SQL median |
|---|---:|---:|---:|---:|---:|
| `release` | 210.894 s | 10.31 MiB | 21.82 ms | 24.99 ms | 45.26 ms |
| `release-fast` | 198.797 s | 12.39 MiB | 24.38 ms | 28.38 ms | 47.15 ms |
| `dev-fast` | 169.289 s | 21.21 MiB | 26.49 ms | 30.45 ms | 49.34 ms |

Relative to `release`, `release-fast` saved about 5.7% of cold build time
while growing about 20.2% and slowing the parsing/search probes by roughly
12-14%. That is not a compelling production trade-off.

`dev-fast` saved about 19.7% of the complete cold build. Its binary was about
106% larger and the parsing/search probes were about 21-22% slower, with SQL
about 9% slower. Those costs are acceptable only in a development context,
where feedback latency matters more than artifact size or peak runtime.

The stronger development optimization is still dependency narrowing: engine
work should use the engine-only path rather than compile the complete SQL/MCP
product under any profile. `dev-fast` is for occasions when a developer
actually needs the complete binary during an edit loop.

Hosted-runner absolute times vary. The workflow records raw JSON and Cargo
timing HTML so future runs can compare the same scenarios instead of treating
one run as a permanent benchmark.

No result in this experiment changes `[profile.release]` automatically.

The CI measurement helpers are PEP 723 scripts and are executable in Git, matching the repository's existing script contract.
