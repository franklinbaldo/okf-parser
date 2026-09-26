---
type: Documentation
title: Native engine selection
description: How okf-parser installs and discovers its Rust engine, and where TypeScript falls back
---

# Native engine selection

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

Resolution is deterministic:

1. an explicit `rust_core` / `rustCore` expert override;
2. a release-matched native engine installed by the Python or npm distribution (`okf-parser` for Python, `okf-core` inside the npm platform package);
3. the `OKF_CORE` environment override;
4. the distribution-specific executable on `PATH`.

**Python has no fallback.** The binary is the implementation
([RFC 0024](../rfcs/0024-rust-native-core.md)); if none is found, `load_bundle`
raises `NativeBinaryMissingError`. Every answer carries a protocol version, and a
binary that speaks a different one is rejected rather than misread.

**TypeScript** falls back to its portable implementation after step 4;
`engine: "native"` selects that implementation explicitly.

If a selected Rust engine starts and fails, the request fails rather than silently changing engines midway through one load.

## Python packaging

`okf-parser` is the only Python distribution and `okf-parser` is its only installed entry point. The platform wheel uses that executable for the ordinary CLI, `serve` MCP command, and private Rust-engine operations; there is no separate `okf-parser-native` PyPI project or runtime dependency. Maturin installs the executable into the active interpreter's scripts directory, which the loader checks directly, so virtual environments work without deployment-specific paths.

A source installation builds that same executable as part of building the `okf-parser` wheel. Release automation must test both the platform wheel and source distribution as fresh consumers before publication.

## npm packaging

`@franklinbaldo/okf-parser` declares platform packages as optional dependencies. The first supported target is `@franklinbaldo/okf-parser-native-linux-x64`, which contains the release-matched executable under `bin/okf-core`. The TypeScript resolver discovers that package directly from `node_modules` before consulting environment overrides.

Installing with `--omit=optional`, or running on a platform for which no native companion is published, keeps the same `loadBundle()` API and uses the portable TypeScript implementation.

The npm native companion never compiles Rust during package installation or application startup. Release automation builds the executable once and tests the exact packaged artifact in a fresh consumer environment.
