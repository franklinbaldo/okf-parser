---
type: Documentation
title: okf-parser native Linux x64 package
description: Platform companion that ships the release-matched okf-core executable for npm consumers
---

# okf-parser-native-linux-x64

Platform companion for `okf-parser` on Linux x64.

This package contains the release-matched `okf-core` executable. It is installed as an optional dependency of `okf-parser`; applications should not depend on it directly or hard-code its path. `loadBundle()` discovers it automatically and falls back to the portable TypeScript engine when no compatible native package is installed.

The companion version always matches the `okf-parser` release that consumes it, so the resolver never needs to negotiate protocol compatibility at runtime.

The executable is built during the release workflow. Rust is never compiled during application installation or startup.
