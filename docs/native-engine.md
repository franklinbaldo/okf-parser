---
type: Documentation
title: Automatic native engine selection
description: How okf-parser installs, discovers, and falls back from the Rust okf-core engine
---

# Automatic native engine selection

Applications should call the ordinary public loaders:

```python
from okf_parser import load_bundle

bundle = load_bundle(root)
```

```ts
import { loadBundle } from "okf-parser";

const bundle = await loadBundle(root);
```

No application code needs to locate `okf-core`.

With the default `engine="auto"` policy, an explicit expert override wins first. The parser then looks for a release-matched engine installed by its own distribution, followed by `OKF_CORE` and `okf-core` on `PATH`. When no compatible native engine exists, Python and TypeScript keep their portable implementations.

`engine="native"` means language-native Python or TypeScript and deliberately skips every Rust probe. It is the deterministic escape hatch for tests, unsupported platforms, and environments that do not permit subprocesses.

## Python packaging

`okf-parser` depends on the exact same version of `okf-parser-native`. Supported platforms receive a platform wheel containing the prebuilt `okf-core`; unsupported platforms can receive the universal selector stub. The loader checks the active interpreter's scripts directory, so virtual environments work without deployment-specific paths.

## npm packaging

`okf-parser` declares platform packages as optional dependencies. The first supported target is `okf-parser-native-linux-x64`, which contains the release-matched executable under `bin/okf-core`. The TypeScript resolver discovers that package directly from `node_modules` before consulting environment overrides.

Installing with `--omit=optional`, or running on a platform for which no native companion is published, keeps the same `loadBundle()` API and uses the portable TypeScript implementation.

Native companions never compile Rust during package installation or application startup. Release automation builds the executable once and tests the exact packaged artifact in a fresh consumer environment.
