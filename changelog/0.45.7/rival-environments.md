---
type: Release Note
title: give every benchmarked rival its own environment and its own configuration
---

- Provision one virtual environment per rival and install each from PyPI inside the benchmark, instead of relying on a prepared environment. Three of these tools -- `okf-cli`, `okf-retrieve` and `okf-generator` -- install an executable named `okf`, so a shared environment could only ever measure whichever was installed last.
- Pass each rival the configuration it requires. `okf-nav` reads bundles from an `OKF_BUNDLES_DIR` rather than from an argument; the first published matrix passed the bundle positionally, the tool correctly reported finding none, and the harness recorded that as an incapability. Shown the fixture the way it expects, it answers the concept count and the type distribution correctly. The same run already gave `okflint` the manifest it demands, which made the omission an inconsistency rather than a uniform limit.
- Measure `okf-generator` 0.1.53, which generates OKF v0.2 bundles and advertises `lookup`, `diff --impact`, `visualize` and `mcp`. It reads the whole tree and answers the concept count and type distribution exactly; its `--impact` traces dependency concepts from code indexing rather than authored links, so it answers a different question rather than a wrong one.
- Measure `okf-cli` 0.6.1, which the executable-name collision had made unmeasurable.
- Record which `okf-parser` each run actually loaded, with its version and import location. The benchmark is a PEP 723 script resolving its dependency from an index, so it measures the published package rather than the working tree, and saying it can fail the current checkout was imprecise. The new field immediately caught a run measuring 0.45.5 while 0.45.6 was current.
