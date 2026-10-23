---
type: Release Note
title: Optional Rust build capabilities
---

- Make the Rust SQL and MCP dependency groups optional while keeping the public `okf-parser` binary on the complete `full` feature set.
- Add an engine-only library facade and a CI contract proving that engine, SQL, MCP, and full feature graphs do not pull unrelated heavy dependencies.
- Preserve dynamic official builds by replacing the old `--no-default-features` convention with `--no-default-features --features full`.
- Document the development commands for engine-only, SQL-only, MCP-only, and complete dynamic builds.\n