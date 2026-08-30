---
name: codebase-to-okf
type: Skill
title: Codebase to OKF
description: >-
  Project source code into a derived OKF bundle while keeping language-specific extraction outside
  okf-parser core. Use when an agent needs compact, queryable code structure with explicit source
  provenance and honest resolution status.
when_to_use: >-
  Use when source code should become disposable OKF knowledge. The bundled Python recipe extracts
  modules, classes, functions, methods, imports, signatures, parameters, returns, docstrings,
  decorators, bases, fields and syntactic call observations.
scripts:
  - scripts/python_codebase_to_okf.py
  - scripts/finalize_codebase_okf.py
  - scripts/query_codebase_okf.py
compatibility: >-
  Standalone execution requires uv and Python 3.12+. The PEP 723 recipes install okf-parser for
  themselves and require no credentials after dependencies are available.
---

# Project a codebase into OKF

Treat the codebase as **authored source** and the generated bundle as **derived, disposable
knowledge**.

Keep this boundary:

```text
source code
  → source-specific recipes in this skill
  → derived OKF concepts and observations
  → okf-parser generic validation / graph / relations / schema / search
```

Do not add Python AST, Tree-sitter grammars, LSP clients, compiler APIs, or producer-specific code
types to `okf-parser` merely to enrich this recipe. Promote a primitive only if it would still be a
good parser feature for unrelated producers such as OpenAPI, SQL schemas, legislation, or API data.

## Generate a Python projection

The executable recipes are bundled with the skill rather than embedded in this document so an
agent does not need to ingest implementation code merely to discover or use the capability.

```bash
uv run skills/codebase-to-okf/scripts/python_codebase_to_okf.py \
  ./src ./.derived/codebase-okf
```

Regeneration is explicit:

```bash
uv run skills/codebase-to-okf/scripts/python_codebase_to_okf.py \
  ./src ./.derived/codebase-okf --force
```

Use repeated `--exclude-dir NAME` for project-specific generated or vendor directories.

## Finalize producer-defined types

After generation, make the derived bundle self-describing with the same type-spec lifecycle used by
`okf-parser` itself:

```bash
uv run skills/codebase-to-okf/scripts/finalize_codebase_okf.py \
  ./.derived/codebase-okf
```

The finalizer does not invent type-spec paths. It reuses the application service behind
`okf-parser init`, lets that service scaffold the missing `docs/types/{slug}.md` files, authors the
code-domain semantics only after the canonical paths exist, and repeats until the introduced `Spec`
type is itself specified. It then requires normative coverage equivalent to:

```bash
uv run okf-parser check ./.derived/codebase-okf \
  --require-spec 'docs/types/{slug}.md' --normative-spec
```

Running the finalizer again is intentionally a no-op when the bundle is already complete.

## Query before reopening source

Use the code-aware query recipe to keep agent context small. It loads the bundle through the public,
generic `okf-parser` `Bundle` API and applies only code-domain filters inside this skill.

```bash
# Symbol lookup
uv run skills/codebase-to-okf/scripts/query_codebase_okf.py \
  ./.derived/codebase-okf --name hello

# Which lexical callers contain a call named hello?
uv run skills/codebase-to-okf/scripts/query_codebase_okf.py \
  ./.derived/codebase-okf --callee hello

# Other useful filters
uv run skills/codebase-to-okf/scripts/query_codebase_okf.py \
  ./.derived/codebase-okf --type CodeClass --source services/
```

The default result is compact JSON. Add `--full` only when the complete concept body/frontmatter is
needed. Prefer querying the derived bundle before reading source; open source when the projection is
insufficient for the task.

## What the generator emits

The Python reference frontend uses the standard-library AST and emits producer-defined types:

- `CodeModule` — one per parsed Python module;
- `CodeClass` — class definitions, including bases, decorators and direct class fields;
- `CodeFunction` — functions outside an immediate class scope;
- `CodeMethod` — methods, including signature, parameters, return annotation and docstring;
- `CodeImport` — import statements with source location and unresolved status;
- `CodeCall` — syntactic call observations with caller, callee text, expression and source location.

Symbols retain source-relative path, qualified name and line range. Legal same-name redefinitions
remain distinct through source-line identity.

## Calls: evidence before resolution

A `CodeCall` is deliberately **not** a resolved dispatch edge. For example, source containing
`Greeter().hello()` produces an observation whose callee is `hello`, expression is
`Greeter().hello`, caller identifies the containing function, and resolution is
`syntactic-unresolved`.

The recipe also links name-matched symbols as **candidates**. These are navigation hints, not claims
that Python runtime dispatch reaches that target. This lets an agent answer questions such as
"which functions contain a call named `hello`?" without reopening source while preserving the
difference between syntax evidence and semantic resolution.

A later recipe may use Pyright, an LSP, SCIP/LSIF, compiler metadata, or another resolver to produce
stronger relations with appropriate provenance. Keep that resolver in the skill unless it exposes
a genuinely source-neutral primitive missing from `okf-parser`.

## Agent workflow

1. Run the narrowest recipe that supports the source language.
2. Finalize the generated bundle so every producer-defined type has a canonical normative spec.
3. Inspect the compact JSON summaries and validation result.
4. Query the generated OKF before opening source files.
5. Open source only when the generated facts are insufficient for the task.
6. Treat candidate targets as candidates until a resolver establishes a stronger relation.
7. Delete and regenerate derived knowledge whenever source or extraction policy changes.

## Guardrails

- Never treat generated OKF as more authoritative than the source it projects.
- Do not execute an untrusted recipe merely because it has PEP 723 metadata.
- Do not put absolute paths, timestamps, credentials, or machine-specific state in canonical output.
- Do not use `--force` with a destination you have not verified.
- Do not turn unresolved names or dynamic Python behavior into hard graph edges without evidence.
- Keep language-specific dependencies in recipe PEP 723 metadata, not in `okf-parser` runtime.

## Definition of done

A codebase projection is complete when extraction succeeds atomically, output is deterministic for
unchanged input, every concept retains source-relative provenance, structural Markdown links
resolve, every producer-defined type in use has a canonical normative specification,
`okf-parser` reports the bundle conformant, and semantic claims do not exceed the evidence available
to the chosen frontend.
