---
type: Release Note
title: Self-describing codebase-to-OKF bundles
description: Generated codebase bundles can now be finalized with canonical type specifications scaffolded through the same init lifecycle used by okf-parser itself
---

# Self-describing codebase-to-OKF bundles

Adds a `finalize_codebase_okf.py` PEP 723 recipe that runs the canonical `init_bundle` service to a fixed point, authors semantics for the generated `Code*` types plus `Spec`, and validates the result with normative `docs/types/{slug}.md` coverage.

The finalizer is deterministic and idempotent: it never invents specification paths, does not rewrite already-complete specs, and a second run leaves the generated bundle byte-for-byte unchanged.
