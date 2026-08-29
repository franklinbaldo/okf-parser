---
type: Spec
title: Project
description: The repository's own root description
---

# Project

A `Project` concept describes the repository itself. There is exactly one, the
root [`README.md`](../../README.md), and there is no reason for a second.

## Frontmatter

- `type` — always `Project`.
- `title` — the project name.
- `description` — one sentence on what the project does.

## Why the README carries frontmatter at all

It makes the repository root readable as an OKF bundle without a special case:
the README is a concept like any other rather than a file the parser has to be
told to ignore. That is the same argument the profile in RFC 0017 generalizes.
