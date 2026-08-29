---
type: Spec
title: Documentation
description: A document explaining how to use something that already exists
---

# Documentation

A `Documentation` concept explains how to use a capability that already exists.
Concepts of this type live in `docs/` and in the repository root.

## Frontmatter

- `type` — always `Documentation`.
- `title` — what is being explained.
- `description` — one sentence on the reader's question it answers.

## Distinguished from its neighbours

- Against `Architecture`: documentation describes, architecture constrains.
- Against `RFC`: an RFC argues for a change and may be rejected. Documentation
  describes what shipped, so a merged RFC does not become documentation by being
  accepted — someone has to write the documentation.
- Against `Spec`: a `Spec` says what one concept type means. Documentation
  explains a command, a workflow or a subsystem.
