---
type: RFC
title: Self-contained recipe skills for source-to-OKF adapters
status: proposed
description: Keep source-specific extraction outside okf-parser core by publishing typed Agent Skills with bundled PEP 723 recipes that generate derived OKF bundles and validate them through the parser
---

# RFC 0019: Self-contained recipe skills for source-to-OKF adapters

## Summary

`okf-parser` should remain a generic parser, validator, relational compiler, graph projection,
schema exporter and retrieval substrate. Source-specific extraction belongs in recipe skills unless
the work exposes a genuinely source-neutral primitive that would remain useful if the motivating
adapter disappeared.

The distribution unit is a typed Agent Skill directory:

```text
source material
    ↓
typed Agent Skill
    └── scripts/<recipe>.py  # PEP 723
    ↓
derived OKF bundle
    ↓
okf-parser
    ↓
validation / relations / graph / schema / search
```

The initial `codebase-to-okf` skill demonstrates the boundary for Python source. `SKILL.md` remains
small, authored and queryable as `type: Skill`; executable implementation lives in bundled PEP 723
scripts so agents need not ingest implementation code merely to discover or invoke the skill.

## Decision

### 1. Source-specific adapters default to skills

A source-to-OKF transformation belongs under `skills/<name>/` when its parsing semantics,
dependencies or taxonomy are specific to the source family. Python AST, Tree-sitter grammars, LSP
clients, compiler APIs, package-manifest interpretation and code-specific concept types therefore do
not enter `okf-parser` core merely because they are useful.

The promotion test is:

```text
Would this primitive still be a good okf-parser feature for unrelated producers
such as OpenAPI, SQL schemas, legislation, API data, or authored knowledge?
```

If not, keep it in the skill. Duplication across two similar adapters is not sufficient evidence of
a generic parser primitive.

### 2. The skill directory is self-contained; the Markdown need not embed code

A recipe skill may contain:

```text
skills/<name>/
├── SKILL.md
├── scripts/
│   └── <recipe>.py
└── references/   # optional
```

`SKILL.md` carries discovery, trust boundaries, workflow and interpretation rules. Executable code
belongs in `scripts/` when embedding it would unnecessarily inflate agent context. The directory,
not one Markdown fence, is the self-contained unit.

Repository-owned `SKILL.md` files should remain valid typed OKF concepts when compatible with the
host skill format. They must not be hidden with `.okfignore` merely because they are Agent Skills.

### 3. PEP 723 owns recipe-only dependencies

Python recipes declare their own bounded dependencies. Source-specific packages must not enter the
main project dependency set merely because one recipe needs them. A future Tree-sitter or LSP-backed
recipe can therefore evolve independently of `okf-parser` installation.

### 4. Generated bundles are derived state

Authored source remains authoritative. Generated OKF is disposable and reproducible. Recipes must
use source-relative provenance, deterministic output, no run-local timestamps in canonical content,
safe overwrite behavior, and parser validation before reporting success.

### 5. Producer taxonomies stay producer-defined

The initial recipe emits `CodeModule`, `CodeClass`, `CodeFunction`, `CodeMethod`, `CodeImport`, and
`CodeCall`. These are recipe vocabulary, not normative parser types. The parser must continue to
preserve and expose producer-defined types without gaining code-specific models.

Repository-owned producer types that participate in the repository's normative type-spec policy
must use the parser's own scaffold workflow. Do not hand-create a spec file just to satisfy CI.
Preview and then write the missing specs with:

```bash
uv run okf-parser init . --spec-template 'docs/types/{slug}.md'
uv run okf-parser init . --spec-template 'docs/types/{slug}.md' --write
```

`init` creates the canonical `type: Spec` stub for each missing type. The stub is then authored to
document the producer-defined fields and semantics, and repository conformance is verified with the
same type-spec convention. The `Skill` specification in this change follows that flow.

### 6. Evidence precedes resolution

A source adapter must not manufacture semantic certainty. Syntax facts and resolved semantic
relations are different evidence classes.

The Python recipe therefore emits calls as `CodeCall` observations containing caller, callee name,
call expression and source location with:

```text
resolution: syntactic-unresolved
```

Name-matched symbols may be linked as candidate targets for navigation, but those candidates are
not dispatch claims. A later Pyright/LSP/SCIP/compiler recipe may emit stronger relations when it
can justify them and preserve resolver provenance.

This distinction is intentional: it lets an agent answer questions such as "which functions contain
a call named `hello`?" from derived knowledge without pretending that Python dynamic dispatch was
statically proven.

### 7. Agent usefulness is the optimization target

A tiny concept is not automatically efficient if the agent must reopen source immediately. Recipes
should preserve compact facts that commonly eliminate source reads: signatures, parameter metadata,
return annotations, docstrings, decorators, inheritance/bases, direct class fields, imports and
call observations.

The evaluation metric is the total context and tool work needed for an agent to complete the same
task correctly, not the byte size of generated files in isolation.

### 8. Validation remains generic

Recipes must validate generated bundles through public `okf-parser` surfaces rather than
reimplementing conformance. Validation needs discovered during adapter work should move into core
only when they are source-neutral OKF requirements.

## Initial implementation: `codebase-to-okf`

The bundled Python recipe uses only the standard-library AST plus `okf-parser`. It extracts:

- modules and module docstrings;
- classes, bases, decorators and direct class fields;
- functions and methods;
- deterministic signatures;
- parameter names, kinds, annotations and defaults;
- return annotations and docstrings;
- import observations;
- syntactic call observations and name-matched candidate targets;
- source-relative paths and line ranges.

Legal same-name redefinitions remain distinct through source-line identity. All source files parse
before output mutation, output replacement requires explicit `--force`, unsafe output ancestors are
refused, and unchanged input regenerates byte-for-byte identical output.

## Testing

Repository tests must verify both layers:

1. `SKILL.md` is itself a conformant typed OKF concept;
2. the bundled scripts declare PEP 723 metadata and compile as Python;
3. a representative codebase generates a conformant bundle;
4. signatures, parameters, return annotations, decorators, bases, fields and docstrings survive;
5. same-name definitions remain distinct;
6. `CodeCall` records identify callers and syntax while remaining explicitly unresolved;
7. name-matched candidate links are navigable but labeled non-authoritative;
8. forced regeneration is byte-for-byte deterministic;
9. `okf-parser init` discovers and scaffolds the repository specification for any newly introduced producer type;
10. the whole repository passes the normative type-spec check after the generated stub is documented.

## Non-goals

This RFC does not add a universal code-intelligence engine, standardize code concept types, define
Python dispatch semantics, require Tree-sitter/LSP/compiler dependencies, or require recipe
resources inside installed wheels.

## Consequences

The parser remains source-neutral while recipe skills can become highly capable. Agent context is
smaller because operational instructions and executable implementation are separated. Richer
source facts can evolve quickly in scripts, and only abstractions demonstrated to be useful beyond
the source domain are candidates for promotion into `okf-parser` core.
