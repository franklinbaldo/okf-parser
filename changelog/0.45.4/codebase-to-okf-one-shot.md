---
type: Release Note
title: One-shot codebase-to-OKF projection
description: The codebase skill now provides one agent-facing command that generates and normatively finalizes a Python OKF bundle before returning success
---

# One-shot codebase-to-OKF projection

Adds `codebase_to_okf.py`, a thin PEP 723 orchestrator over the existing Python generator and type finalizer. The default agent workflow now returns only after producer-defined type specs reach their canonical fixed point and normative validation succeeds.

The lower-level generation and finalization recipes remain available for inspection and repair, while `--force` regeneration through the one-shot path remains byte-for-byte deterministic.
