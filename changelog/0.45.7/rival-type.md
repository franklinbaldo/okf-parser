---
type: Release Note
title: record the competitive landscape as queryable OKF concepts
---

- Add a declared `Rival` type, specified in `docs/types/rival.md` with relational columns in `docs/types/rival.schema.sql`, and register eleven OKF tools under `benchmarks/rivals/`. The landscape this project competes in is now part of the bundle it validates, so it can be queried and counted rather than remembered.
- Record five rivals the capability matrix does not yet interrogate with `measured: false`. An unmeasured rival is a gap in the measurement rather than a gap in the data, and writing it down keeps the backlog queryable where prose would omit it and read as complete.
- Keep each rival's advertised `surface` in the concept, because it is the evidence behind an unsupported verdict: a reader can only audit that claim by seeing which subcommands existed when the matrix ran. How to interrogate a rival stays in the benchmark, since adapters include callables no frontmatter can hold.
- Add `tests/test_rivals_registry.py`, which reads the registry through `okf-parser` itself and fails when the registered rivals and the benchmark's adapters disagree about which tools are measured or what surfaces they expose. It caught two drifted surfaces on its first run.
