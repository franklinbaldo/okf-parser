---
type: Spec
title: Procedure
description: A reusable operational procedure documented as an ordinary OKF concept
---

# Procedure

A `Procedure` concept describes a repeatable operational activity: what is done, why it is done, and any relationships to other procedures or knowledge needed to perform it.

The minimal example bundle uses this type for its two ordinary concepts under `examples/minimal/concepts/`.

## Frontmatter

- `type` — always `Procedure`.
- `title` — human-readable name of the procedure.
- `description` — concise statement of the procedure's purpose.
- additional producer-defined fields are allowed. The example deliberately uses `owner` to demonstrate that OKF preserves vocabulary it does not define.

## Relationships

Relations between procedures use ordinary Markdown links. The parser projects resolvable links into the bundle graph; the type itself does not define a separate relation syntax.

## Scope

This specification documents the `Procedure` type used by the markerless compatibility example. It does not make `Procedure` a reserved OKF core type and does not depend on the proposed `.fact/` profile semantics in RFC 0017.
