---
type: Spec
title: Skill
description: A repository-owned Agent Skill exposed as ordinary typed OKF knowledge
---

# Skill

A `Skill` concept describes an agent-facing capability that combines compact authored instructions with optional executable or reference resources in the same skill directory.

`Skill` is producer-defined vocabulary used by this repository. It is not a reserved OKF core type, and `okf-parser` does not interpret Agent Skill semantics beyond preserving and exposing the authored concept like any other producer-defined type.

## Frontmatter

Repository-owned skills use these fields:

- `type` — always `Skill`.
- `name` — stable skill identifier used by the host skill convention.
- `title` — human-readable skill name.
- `description` — concise discovery-oriented description of the capability.
- `when_to_use` — guidance for deciding when an agent should invoke the skill.
- `scripts` — optional repository-relative resources bundled with the skill.
- `compatibility` — optional runtime or environment requirements.
- additional producer-defined fields are allowed.

The body contains the operational instructions an agent should read. Executable implementation may live under the skill directory, for example `scripts/`, so discovering the skill does not require loading implementation code into agent context.

## Type-spec workflow

When a new producer-defined type is introduced in this repository, its required specification path should be scaffolded by `okf-parser` rather than created by hand:

```bash
uv run okf-parser init . --spec-template 'docs/types/{slug}.md' --write
```

The generated `type: Spec` stub is then edited to document that type's fields and semantics. Repository CI verifies the resulting bundle with the same `docs/types/{slug}.md` convention using normative specification checks.

## Scope

This specification documents the repository's `Skill` concepts, including `skills/codebase-to-okf/SKILL.md`. It does not standardize Agent Skills for all OKF producers and does not move source-specific recipe behavior into `okf-parser` core.
