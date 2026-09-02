---
type: Documentation
title: Automatic native engine selection
description: How okf-parser installs, discovers, and falls back from its Rust engine
---

# Automatic native engine selection

Applications should call the ordinary public loaders:

```python
from okf_parser import load_bundle

bundle = load_bundle(root)
```

```ts
import { loadBundle } from "@franklinbaldo/okf-parser";

const bundle = await loadBundle(root);
```

No application code needs to locate a Rust executable.

With the default `engine="auto"` policy, resolution is deterministic:

1. an explicit `rust_core` / `rustCore` expert override;
2. a release-matched native engine installed by the Python or npm distribution (`okf-parser` for Python, `okf-core` inside the npm platform package);
3. the `OKF_CORE` environment override;
4. the distribution-specific executable on `PATH`;
5. the portable Python or TypeScript implementation.

If a selected Rust engine starts and fails, the request fails rather than silently changing engines midway through one load.

`engine="native"` means language-native Python or TypeScript and deliberately skips every Rust probe. It is the deterministic escape hatch for tests, unsupported platforms, and environments that do not permit subprocesses.

## Python packaging

`okf-parser` is the only Python distribution and `okf-parser` is its only installed entry point. The platform wheel uses that executable for the ordinary CLI, `serve` MCP command, and private Rust-engine operations; there is no separate `okf-parser-native` PyPI project or runtime dependency. Maturin installs the executable into the active interpreter's scripts directory, which the loader checks directly, so virtual environments work without deployment-specific paths.

A source installation builds that same executable as part of building the `okf-parser` wheel. Release automation must test both the platform wheel and source distribution as fresh consumers before publication.

## npm packaging

`@franklinbaldo/okf-parser` declares platform packages as optional dependencies. The first supported target is `@franklinbaldo/okf-parser-native-linux-x64`, which contains the release-matched executable under `bin/okf-core`. The TypeScript resolver discovers that package directly from `node_modules` before consulting environment overrides.

Installing with `--omit=optional`, or running on a platform for which no native companion is published, keeps the same `loadBundle()` API and uses the portable TypeScript implementation.

The npm native companion never compiles Rust during package installation or application startup. Release automation builds the executable once and tests the exact packaged artifact in a fresh consumer environment.
