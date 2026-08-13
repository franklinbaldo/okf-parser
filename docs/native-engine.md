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

With the default `engine="auto"` policy, resolution is deterministic:

1. an explicit `rust_core` / `rustCore` expert override;
2. a release-matched native engine installed by the Python or npm distribution;
3. the `OKF_CORE` environment override;
4. `okf-core` on `PATH`;
5. the portable Python or TypeScript implementation.

If a selected Rust engine starts and fails, the request fails rather than silently changing engines midway through one load.

`engine="native"` means language-native Python or TypeScript and deliberately skips every Rust probe. It is the deterministic escape hatch for tests, unsupported platforms, and environments that do not permit subprocesses.

## Python packaging

`okf-parser` is the only Python distribution. Its platform wheel contains the release-matched `okf-core` executable alongside the Python package; there is no separate `okf-parser-native` PyPI project or runtime dependency. Maturin installs the executable into the active interpreter's scripts directory, which the loader checks directly, so virtual environments work without deployment-specific paths.

A source installation builds that same executable as part of building the `okf-parser` wheel. Release automation must test both the platform wheel and source distribution as fresh consumers before publication.

## npm packaging

`okf-parser` declares platform packages as optional dependencies. The first supported target is `okf-parser-native-linux-x64`, which contains the release-matched executable under `bin/okf-core`. The TypeScript resolver discovers that package directly from `node_modules` before consulting environment overrides.

Installing with `--omit=optional`, or running on a platform for which no native companion is published, keeps the same `loadBundle()` API and uses the portable TypeScript implementation.

The npm native companion never compiles Rust during package installation or application startup. Release automation builds the executable once and tests the exact packaged artifact in a fresh consumer environment.
