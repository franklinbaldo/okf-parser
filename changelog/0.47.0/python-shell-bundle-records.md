---
type: Release Note
title: Bundles load through the native binary and are typed records
---

# Bundles load through the native binary and are typed records

Phase 2 of [RFC 0024](../../rfcs/0024-rust-native-core.md), now revised as
*Rust core, Python shell*: the native binary is the product, and the Python
package is a thin shell over it, the way `ruff` and `uv` ship.

Breaking:

- `load_bundle` always calls the native binary installed with the package. The
  pure-Python ingestion path and `load_bundle(engine=...)` are removed; a
  missing binary raises `NativeBinaryMissingError`.
- `Bundle.concepts`, `.links` and `.reserved` are tuples of `ConceptRecord`,
  `LinkRecord` and `ReservedRecord` instead of ibis tables. There is no
  `.execute()` shim: read the records directly, or query them as SQL through
  `attach_okf`.
- Frontmatter with a YAML tag that has no JSON representation (`!!binary`,
  `!!set`, application tags) is reported as `OKF001` by the native engine too,
  matching the Python engine it replaces.

The binary's answers now carry a protocol version, validated by the shell, and
include the graph summary, so `Bundle.graph().summary()` no longer computes
anything in Python. Loading a bundle no longer builds ibis tables, which makes
the test suite about 40% faster.
