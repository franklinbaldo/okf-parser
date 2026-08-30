---
type: RFC
title: Self-contained recipe skills for source-to-OKF adapters
status: proposed
description: Keep source-specific extraction outside okf-parser core by publishing executable Agent Skills whose deterministic PEP 723 recipes generate derived OKF bundles and validate them through the parser
---

# RFC 0019: Self-contained recipe skills for source-to-OKF adapters

## Summary

`okf-parser` is useful as a generic parser, validator, relational compiler, graph projection,
schema exporter and retrieval substrate. It should not become the place where every upstream
source format acquires its own parser.

A codebase is the motivating case. Turning Python, TypeScript, Rust or Java source into concepts
can be valuable to an agent, but Tree-sitter grammars, language servers, compiler APIs and
source-specific heuristics are not generic OKF runtime dependencies.

This RFC makes a different distribution unit first-class in the repository:

```text
source material
    ↓
Agent Skill + embedded PEP 723 recipe
    ↓
derived OKF bundle
    ↓
okf-parser
    ↓
validation / relations / graph / schema / search
```

The skill owns source-specific extraction. `okf-parser` owns the generic OKF boundary. The first
reference implementation is `skills/codebase-to-okf/SKILL.md`, whose embedded Python recipe
projects Python source into a disposable OKF bundle and validates the result with `okf-parser`.

## Motivation

There are two tempting but undesirable ways to add codebase generation.

The first is to add Python AST, Tree-sitter grammars, LSP clients and language-specific symbol
models directly to `okf-parser`. That makes every extraction domain a permanent parser dependency
and turns a domain-neutral package into a growing collection of frontends.

The second is to put standalone scripts under `examples/` or `scripts/`. That keeps the core
small, but loses the agent-facing operational knowledge: when the adapter is appropriate, which
facts are trustworthy, which output is derived, how to validate it, what not to infer and how to
adapt the recipe safely.

An Agent Skill is the better boundary because it can carry both the instructions and the
executable reference recipe. PEP 723 makes the executable part self-describing without adding its
dependencies to `pyproject.toml` or `uv.lock`.

## Decision

### 1. Source-specific adapters default to skills, not parser-core modules

A deterministic transformation from some external source into OKF should normally live under:

```text
skills/<recipe-name>/SKILL.md
```

when all of the following are true:

- the source has domain-specific parsing or extraction semantics;
- the result can be represented through existing OKF concepts, links, schemas or projections;
- `okf-parser` can already validate and consume the generated bundle through generic surfaces;
- adding the source dependency to the core would benefit only that source family.

The existence of several useful recipes is not by itself a reason to add several source parsers to
the runtime.

### 2. The skill is the distribution unit

A recipe skill contains the operational contract and may contain its executable reference
implementation directly in `SKILL.md` as a fenced Python block with PEP 723 metadata.

The repository does not require a sibling `scripts/` directory for such a recipe. An agent
materializes the marked code block to a temporary `.py` file and runs it with `uv run`.

This is intentionally different from making the Markdown code block decorative documentation.
The embedded program is tested as executable source.

### 3. PEP 723 contains recipe-only dependencies

An embedded Python recipe declares a bounded dependency range whose lower bound is an already
published release providing the generic API it consumes. For the initial recipe:

```python
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "okf-parser>=0.45.2,<0.46",
# ]
# ///
```

Source-specific dependencies belong in that block. They do not enter the main package dependency
set merely because one recipe needs them. The recipe must not require an unreleased repository
version merely because it happens to be authored in that development cycle.

A future Tree-sitter recipe can therefore carry its own grammar packages; a compiler-backed Rust
recipe can choose a different implementation; neither choice changes the `okf-parser` install.

### 4. Authored source remains authoritative; generated OKF is derived state

A recipe-generated bundle is a projection of another source, not a second authored truth. Unless
a recipe explicitly says otherwise, consumers must assume it is disposable and reproducible from
its source plus recipe version/configuration.

A recipe should therefore:

- preserve source-relative provenance such as file path and symbol name;
- avoid embedding machine-specific absolute paths in generated concepts;
- generate deterministic paths and content for unchanged input;
- omit timestamps and other run-local noise from canonical output;
- refuse unsafe destructive overwrite by default;
- validate the completed bundle with `okf-parser` before reporting success.

### 5. Recipe taxonomies are producer-defined, not OKF core types

The initial codebase recipe emits the producer-defined types:

```text
CodeModule
CodeClass
CodeFunction
CodeMethod
CodeImport
```

These names are an example vocabulary, not normative OKF taxonomy. The parser does not gain Python
classes or a `CodeModule` model because this recipe uses them.

The same rule applies to future recipes for OpenAPI, SQL schemas, GitHub repositories, legal
sources or other domains.

### 6. Do not manufacture semantic certainty

A source adapter records only facts its frontend can support. The first Python recipe uses the
standard-library AST for definitions and import statements. It does not claim a resolved call
graph, dynamic dispatch, runtime import targets or semantic equivalence between names merely
because those would make the graph richer.

A stronger frontend may add those facts later when it has a resolver that can support them and
preserve their provenance.

### 7. Validation is part of the recipe contract

Successful generation means more than writing Markdown. Before returning success, the recipe must
run the generated output through a current `okf-parser` validation surface and return non-zero when
normative OKF errors remain.

The initial recipe calls the public `validate_path()` API. A recipe may instead use the CLI when
that is the more natural boundary, but it must not reimplement OKF conformance locally.

### 8. Embedded recipes are executable code

A host or agent must not automatically execute an untrusted skill merely because it contains a
PEP 723 block. Normal executable-code trust rules still apply.

For repository-owned recipes, the intended workflow is:

1. read the skill and identify the explicitly marked recipe block;
2. materialize that block to a temporary script;
3. execute it with `uv run` against an explicit source and output path;
4. inspect its result and parser diagnostics;
5. remove the temporary script when it is no longer needed.

The recipe must not require credentials or network access unless the skill explicitly declares
that requirement. The initial codebase recipe requires neither.

### 9. Promotion into core requires a generic primitive

Real use may expose repeated friction across several recipes. Promotion is justified when the
reusable abstraction is itself source-neutral—for example, a generic provenance primitive,
transactional bundle writer, schema compiler or retrieval operation.

The promotion test is:

```text
Would this primitive still belong in okf-parser if the motivating source adapter disappeared?
```

If the answer is no, keep it in the skill.

## Initial implementation: `codebase-to-okf`

Phase 1 ships one executable reference recipe for Python codebases. It deliberately chooses the
standard-library `ast` module instead of a multi-language parsing dependency so the architectural
boundary is visible before the project optimizes breadth.

For each discovered Python file it emits one `CodeModule`; definitions become `CodeClass`,
`CodeFunction` or `CodeMethod`; import statements become `CodeImport`. Markdown links connect each
observation back to its generated module concept and connect nested symbols to generated parents
when that relationship is structurally known.

The recipe:

- ignores common dependency/build/cache directories and accepts repeated `--exclude-dir` values;
- uses source-relative paths and deterministic hashed filenames;
- includes a definition's source line in generated symbol identity, so legal Python redefinitions
  with the same qualified name do not silently overwrite each other;
- parses all source files before mutating the output, so a syntax/UTF-8 failure does not create a
  partial projection;
- refuses a non-empty output unless `--force` is explicit;
- refuses output locations that could delete the source tree;
- emits a deterministic reserved `index.md`;
- validates the result with `validate_path()` and reports a compact JSON summary.

This implementation is a reference recipe, not a promise that Python AST is the preferred frontend
for every code-intelligence workload.

## Testing

Repository tests must treat the embedded code as code rather than prose. The implementation test:

1. extracts the uniquely marked Python fence from `SKILL.md`;
2. asserts that the PEP 723 script metadata is present and names an installable release range;
3. executes the extracted program against a temporary Python codebase that includes a legal symbol
   redefinition;
4. verifies the generated bundle is conformant through `okf-parser` and that both definitions
   survive as distinct concepts;
5. reruns with `--force` and verifies byte-for-byte deterministic output.

CI does not need to invoke `uv` or download the package to test this repository-owned recipe; PEP
723 is comment syntax to Python, so the extracted program can execute in the already provisioned
project test environment. The PEP 723 declaration governs copied/standalone use.

## Packaging and discovery

Recipe skills are repository resources and examples. They are not part of the stable Python import
API and do not need to be present in an installed wheel. Their canonical location is the GitHub
repository at the commit/tag being consulted.

A future skill registry or packaging mechanism may distribute them separately without changing the
core/parser boundary defined here.

## Non-goals

This RFC does not:

- add a universal code-intelligence engine to `okf-parser`;
- standardize `CodeModule` or the other example types as part of OKF;
- define a call graph or language-server protocol;
- require Tree-sitter, Pyright, rust-analyzer or any compiler dependency;
- require every example to be an Agent Skill;
- make arbitrary skill code safe to execute;
- require recipe resources to ship inside Python wheels.

## Consequences

The core stays small and source-neutral while the repository can still demonstrate substantial,
agent-usable integrations. Recipes can evolve at the speed of their source ecosystem, carry their
own dependencies, and be copied or adapted without creating permanent runtime coupling.

The cost is that a skill author must maintain the adapter contract and executable recipe together.
That is deliberate: the source-specific knowledge needed to run the adapter correctly belongs next
to the adapter, not hidden inside the parser.
