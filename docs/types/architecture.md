---
type: Spec
title: Architecture
description: A document recording a structural boundary the implementation is expected to hold
---

# Architecture

An `Architecture` concept records a structural rule the implementation is
expected to hold, in enough detail that a pull request can be judged against it.

The single concept of this type is [`docs/architecture.md`](../architecture.md),
which draws the boundary between the strict authored-OKF core and source
adapters.

## Frontmatter

- `type` — always `Architecture`.
- `title` — the boundary being drawn.

`description` is optional here and the existing document omits it.

## Distinguished from Documentation

`Documentation` explains how to use what exists. `Architecture` constrains what
may be built. A rule that no reviewer would cite when rejecting a change is
documentation, not architecture.
