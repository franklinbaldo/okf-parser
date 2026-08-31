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
  decorators, bases, fields, syntactic call observations, and standard project dependency metadata.
scripts:
  - scripts/codebase_to_okf.py
  - scripts/python_codebase_to_okf.py
  - scripts/python_project_metadata_to_okf.py
  - scripts/finalize_codebase_okf.py
  - scripts/resolve_codebase_okf.py
  - scripts/query_codebase_okf.py
compatibility: >-
  Standalone execution requires uv and Python 3.12+. The PEP 723 recipes install okf-parser and any
  source-specific parser dependencies for themselves and require no credentials after dependencies
  are available.
---

# Project a codebase into OKF

Treat the codebase as **authored source** and the generated bundle as **derived, disposable
knowledge**.

Keep this boundary:

```text
source code + authored project metadata
  → source-specific recipes in this skill
  → derived OKF concepts and observations
  → optional source-specific resolution claims
  → okf-parser generic validation / graph / relations / schema / search
```

Do not add Python AST, Tree-sitter grammars, LSP clients, compiler APIs, package-manifest semantics,
or producer-specific code types to `okf-parser` merely to enrich this recipe. Promote a primitive
only if it would still be a good parser feature for unrelated producers such as OpenAPI, SQL
schemas, legislation, or API data.

## Generate a Python projection

The default agent-facing recipe is one-shot: it generates the source projection, adds standard
PEP 621 project metadata when a `[project]` table exists in `pyproject.toml`, scaffolds and authors
the producer-defined type specs through the canonical `okf-parser init` lifecycle, and only returns
success after normative validation passes.

```bash
uv run skills/codebase-to-okf/scripts/codebase_to_okf.py \
  ./src ./.derived/codebase-okf
```

Regeneration is explicit:

```bash
uv run skills/codebase-to-okf/scripts/codebase_to_okf.py \
  ./src ./.derived/codebase-okf --force
```

Use repeated `--exclude-dir NAME` for project-specific generated or vendor directories. A successful
JSON result reports source concepts, manifest concepts, project/dependency counts, generated
specification count, and `normative_specs: true`.

## Project manifest evidence

When the source root contains standard PEP 621 metadata, the one-shot recipe emits one `CodeProject`
plus one `CodeDependency` for each runtime or optional dependency declaration. The original PEP 508
requirement string is preserved together with parsed navigation fields such as distribution name,
extras, version specifier, marker, and direct URL when present.

The authority is deliberately narrow:

```text
CodeDependency = declared in authored project metadata
              ≠ installed in an environment
              ≠ imported by source
              ≠ reachable at runtime
              ≠ actually used
```

This separation lets later analysis compare declared dependencies with `CodeImport` observations
without silently equating the two. Project metadata remains source-specific skill logic rather than
an `okf-parser` core concern.

The lower-level manifest projector can be run directly against an already generated, non-normative
bundle when an intermediate workflow needs it:

```bash
uv run skills/codebase-to-okf/scripts/python_project_metadata_to_okf.py \
  ./src ./.derived/codebase-okf
```

## Resolve local imports conservatively

Resolution is a separate enrichment step because syntax observations and inferred relations have
different authority. The first resolver only maps import targets that match a unique `CodeModule`
inside the projected source tree:

```bash
uv run skills/codebase-to-okf/scripts/resolve_codebase_okf.py \
  ./.derived/codebase-okf
```

It never rewrites the original `CodeImport`. Instead it emits separate `CodeImportResolution`
concepts with `source-tree-resolved` or `source-tree-partial` status and a versioned
`resolution_method`. Imports with no local module match remain only as syntax observations.

A source-tree match is deliberately weaker than a runtime import claim: Python import hooks,
`sys.path`, environment differences, rebinding, monkey-patching and call dispatch remain outside
what this resolver asserts.

## Lower-level generation and type finalization

The one-shot recipe intentionally composes smaller recipes. Use them directly only when a task needs
to inspect or repair an intermediate stage:

```bash
uv run skills/codebase-to-okf/scripts/python_codebase_to_okf.py \
  ./src ./.derived/codebase-okf

uv run skills/codebase-to-okf/scripts/python_project_metadata_to_okf.py \
  ./src ./.derived/codebase-okf

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

# Which import-resolution claims point at a projected local module?
uv run skills/codebase-to-okf/scripts/query_codebase_okf.py \
  ./.derived/codebase-okf --dependency pkg.utils

# Which manifest declaration names a package?
uv run skills/codebase-to-okf/scripts/query_codebase_okf.py \
  ./.derived/codebase-okf --package httpx

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
- `CodeImport` — immutable syntax-level import observations;
- `CodeCall` — syntactic call observations with caller, callee text, expression and source location;
- `CodeProject` — authored PEP 621 project metadata when present;
- `CodeDependency` — authored runtime and optional PEP 508 dependency declarations.

Optional enrichment recipes may add claim types such as `CodeImportResolution`; they do not erase or
upgrade the authority of the underlying observations.

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

1. Run the narrowest one-shot recipe that supports the source language.
2. Inspect its compact JSON summary and normative validation result.
3. Use manifest declarations as declaration evidence, never as proof of imports or runtime use.
4. Run an optional resolver only when the task benefits from stronger derived relations.
5. Query the generated OKF before opening source files.
6. Open source only when the generated facts are insufficient for the task.
7. Keep manifest declarations, syntax observations, source-tree resolution and runtime claims
   epistemically distinct.
8. Delete and regenerate derived knowledge whenever source or extraction policy changes.

## Guardrails

- Never treat generated OKF as more authoritative than the source it projects.
- Do not execute an untrusted recipe merely because it has PEP 723 metadata.
- Do not put absolute paths, timestamps, credentials, or machine-specific state in canonical output.
- Do not use `--force` with a destination you have not verified.
- Do not infer installed or used dependencies merely from `pyproject.toml` declarations.
- Do not turn unresolved names or dynamic Python behavior into hard graph edges without evidence.
- Keep language-specific dependencies in recipe PEP 723 metadata, not in `okf-parser` runtime.

## Definition of done

A codebase projection is complete when extraction succeeds atomically, output is deterministic for
unchanged input, every concept retains source-relative provenance, structural Markdown links
resolve, every producer-defined type in use has a canonical normative specification,
`okf-parser` reports the bundle conformant, and semantic claims do not exceed the evidence available
to the chosen frontend, manifest parser, or resolver.
