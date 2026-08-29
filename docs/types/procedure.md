---
type: Spec
title: Procedure
description: A repeatable sequence of steps a person performs, used by the example bundles
---

# Procedure

A `Procedure` is a repeatable sequence of steps a person performs — onboarding a
team member, reviewing access each quarter. It lives in `examples/*/concepts/`.

No `Procedure` describes how this repository is developed. The type exists only
inside the example bundles, where it demonstrates OKF as an ordinary project
would use it: a plain domain noun, unknown to the parser, carrying meaning that
lives in the documents rather than in the tool.

## Frontmatter

- `type` — always `Procedure`.
- `title` — the procedure, named as an action.
- `description` — one sentence on when it is performed.
- `owner` — optional, the team accountable for it. Not part of OKF v0.2 and not
  interpreted by the parser; it is carried in `examples/minimal` on purpose, to
  show that a producer's own vocabulary survives parsing instead of being
  rejected.

## What a Procedure is not

A `Procedure` is not a policy. A policy says what must hold; a procedure says
what someone does. Where the two appear together, the recurring steps are the
procedure and the rule they satisfy belongs elsewhere.

Specifying an example type may look like ceremony, but it is the gate working as
intended: `check --normative-spec` cannot tell an example's vocabulary from the
repository's own, and it should not — a type in use without a document saying
what it means is exactly the drift the gate exists to catch.
