---
type: RFC
title: Bidirectional code generation between OKF bundles and Python models
description: Generate Pydantic models from OKF profiles and OKF profiles from Pydantic models or dataclasses
status: proposed
---

# RFC 0001: Python model code generation

## Summary

Add bidirectional code generation between Open Knowledge Format bundles and
typed Python models:

1. inspect an OKF bundle and generate Pydantic model source code;
2. inspect Pydantic models or dataclasses and generate an OKF profile bundle
   that documents their frontmatter contract.

The generated profile is an extension layered on OKF v0.2. It is not a
replacement for the normative OKF specification. OKF intentionally allows
producer-defined concept types and frontmatter fields, so every inferred type
must remain explicit, reviewable, and reproducible.

## Motivation

OKF bundles are easy to author and exchange, while Pydantic models and
dataclasses are convenient application boundaries. Today, a team must maintain
the same contract twice: once in Markdown conventions and again in Python.

Bidirectional generation can provide:

- typed ingestion of authored bundles;
- human-readable documentation generated from application models;
- contract drift detection in CI;
- a common representation for validators, agents, MCP tools, and DuckDB;
- incremental adoption without restricting ordinary OKF consumers.

## Terminology

- **concept document**: an ordinary OKF Markdown file with frontmatter and body;
- **concept type**: the producer-defined value of the required `type` field;
- **profile**: a machine-readable, OKF-compatible description of the fields
  expected for one or more concept types;
- **model target**: generated Pydantic source code;
- **profile target**: generated OKF Markdown concepts describing Python models.

## Goals

- deterministic output from identical inputs and options;
- support Pydantic v2 `BaseModel` classes and standard-library dataclasses;
- preserve unknown frontmatter fields by default;
- expose inference conflicts instead of silently selecting a lossy type;
- produce readable Python and Markdown suitable for version control;
- support check-only generation for CI;
- make generated artifacts traceable to their source and generator version;
- allow a useful round-trip for the supported type subset.

## Non-goals

- redefining the normative OKF v0.2 specification;
- inferring business semantics from field names with an LLM;
- serializing Python methods, validators, computed fields, or arbitrary code;
- executing untrusted Python merely to inspect its types;
- guaranteeing a lossless round-trip for every Python annotation or YAML value;
- requiring generated profiles for an otherwise conformant OKF bundle.

## Proposed interface

### OKF bundle to Pydantic

```bash
okf-parser codegen pydantic ./knowledge \
  --output ./generated_models.py

okf-parser codegen pydantic ./knowledge \
  --output ./generated_models.py \
  --check
```

The command groups concepts by their `type`, infers a model for each group, and
generates a shared base model plus a type-to-model registry.

### Python model to OKF profile

```bash
okf-parser codegen profile ./src/domain.py \
  --symbol Customer \
  --output ./knowledge/profiles

okf-parser codegen profile ./src/domain.py \
  --all-models \
  --output ./knowledge/profiles \
  --check
```

Static source inspection is the default. A future explicit `--import` mode may
load a module when runtime Pydantic metadata is required. The importing mode
must be documented as trusted-code execution and must never be the default.

### Python API

```python
from pathlib import Path

from okf_parser.codegen import (
    generate_profile_bundle,
    generate_pydantic_models,
)

generate_pydantic_models(
    bundle=Path("knowledge"),
    output=Path("generated_models.py"),
)
generate_profile_bundle(
    source=Path("src/domain.py"),
    output=Path("knowledge/profiles"),
)
```

## Intermediate representation

Both directions must compile through a language-neutral contract graph rather
than translating directly from Markdown to Python:

```text
OKF bundle ────────┐
                   ├─> ContractGraph ─> Pydantic source
Python annotations ┘                 └─> OKF profile bundle
```

The contract graph contains:

- concept type and deterministic Python class name;
- field name, requiredness, nullability, and default;
- scalar, collection, mapping, union, literal, enum, and nested-model types;
- title, description, examples, and deprecation metadata;
- source locations and inference diagnostics;
- references between models;
- extension metadata that cannot be represented directly in OKF v0.2.

Ibis tables should represent contracts and fields for relational checks.
NetworkX should represent model references and detect cycles before source
generation.

## Bundle-to-model inference

### Grouping and naming

Concepts are grouped by the exact frontmatter `type` value. Class names are
derived deterministically using Unicode normalization and PascalCase. Naming
collisions are errors unless an explicit mapping resolves them.

The normative `type` field becomes a discriminator using `Literal` where
possible. Common fields may be placed on a generated `OKFConcept` base class.

### Requiredness

A field present in every successfully parsed concept of a type is required.
A field absent from at least one concept is optional. YAML `null` affects
nullability independently from requiredness.

An empty or single-document group cannot establish a reliable closed schema.
The generator emits an advisory diagnostic and defaults to an open model.

### Type lattice

Observed YAML values are joined using a deterministic widening lattice:

```text
bool → int → float
scalar conflicts → explicit union
homogeneous sequence → list[item]
heterogeneous sequence → list[union]
mapping → generated nested model or dict[str, value]
irreconcilable values → Any plus an error in strict mode
```

Strings remain strings unless an explicit profile or option enables format
inference for dates, datetimes, UUIDs, paths, or URLs. Field-name heuristics are
not used.

### Unknown fields

Generated Pydantic models default to `extra="allow"` because OKF consumers must
tolerate producer extensions. `--forbid-extra` is a profile policy and must not
be presented as normative OKF conformance.

### Bodies and identities

The Markdown body and bundle-derived concept ID are modeled separately from
frontmatter. They must not be inserted into authored frontmatter during a
round-trip.

## Model-to-profile generation

Each selected class produces an OKF concept under a deterministic path such as:

```text
profiles/customer.md
profiles/order.md
```

The profile concept itself has valid OKF frontmatter:

```yaml
---
type: OKFProfile
title: Customer
python_qualname: domain.Customer
concept_type: Customer
---
```

Its Markdown body documents fields, requiredness, defaults, descriptions, and
references. A machine-readable `fields` extension in frontmatter stores the
contract without changing the normative OKF specification.

Pydantic metadata is read from annotations, `Field` declarations, aliases,
descriptions, defaults, `Annotated`, unions, literals, enums, and nested models.
Dataclass metadata uses the same normalized contract representation.

Private attributes, `ClassVar`, computed fields, methods, and runtime validators
are excluded. Unsupported constraints generate diagnostics and are preserved as
opaque extension metadata when possible.

## Round-trip guarantees

The supported subset should satisfy semantic, not textual, round-trip:

```text
Python model → profile → generated model
```

must preserve field names or aliases, supported types, requiredness,
nullability, defaults, descriptions, and references.

```text
OKF bundle → model → profile
```

must preserve the inferred contract and diagnostics, but cannot reconstruct
which values were merely coincidental observations. Generated artifacts include
their source digest, generator version, and options so CI can detect drift.

## File safety

- `--check` performs no writes and fails when generated output differs.
- Writes use temporary files followed by atomic replacement.
- Existing handwritten files are never overwritten unless they contain a
  recognized generation marker or `--force` is explicit.
- Output paths are resolved and checked against the requested output root.
- Generated Python is formatted with Ruff before comparison or writing.
- Generated Markdown is formatted with mdformat before comparison or writing.

## Diagnostics

Code generation uses the existing aggregate diagnostic model. Proposed codes:

- `GEN001`: concept type cannot map to a unique class name;
- `GEN002`: observed values require an unsupported or ambiguous type;
- `GEN003`: insufficient observations for a closed inferred schema;
- `GEN004`: Python annotation is unsupported;
- `GEN005`: model reference cannot be resolved;
- `GEN006`: generated output is stale in check mode;
- `GEN007`: output would overwrite a handwritten file;
- `GEN008`: importing Python was requested for an untrusted source.

Inference warnings do not make the source OKF bundle non-conformant. Strict
code-generation mode may still return a non-zero exit code for them.

## CI integration

Consumers can commit generated artifacts and verify them without rewriting:

```yaml
- run: uvx --from okf-parser okf-parser check knowledge
- run: >-
    uvx --from okf-parser okf-parser codegen pydantic knowledge
    --output generated_models.py
    --check
```

Model-first projects can similarly check that their profile bundle is current.
The composite GitHub Action may expose code-generation checks after the CLI
contract is stable.

## Implementation plan

1. Define immutable contract graph records and Ibis schemas.
2. Implement YAML-value type joining with property-based tests.
3. Generate deterministic Pydantic v2 source from explicit profiles.
4. Add observed-bundle inference and strict diagnostics.
5. Parse dataclasses and common Pydantic declarations with the Python AST.
6. Generate OKF profile concepts and machine-readable field extensions.
7. Add check-only mode, safe writes, formatting, and source digests.
8. Expose CLI, Python API, MCP read operations, and CI examples.
9. Test supported semantic round-trips and unsupported-type diagnostics.

## Acceptance criteria

- repeated generation is byte-for-byte deterministic;
- generated Python passes Ruff format, Ruff check, and ty;
- generated profiles pass `okf-parser check`;
- check mode detects drift and does not change the filesystem;
- arbitrary source modules are not imported by default;
- name collisions and type conflicts are reported with source locations;
- the supported type subset passes semantic round-trip tests;
- unknown OKF frontmatter remains accepted by generated models by default.

## Open questions

1. Should the machine-readable profile extension use JSON Schema as its
   serialized form while retaining the contract graph internally?
2. Should explicit profiles always win over observed-value inference?
3. How should aliases map to authored YAML keys during round-trip?
4. Should nested mappings become named models automatically or remain mappings
   until an explicit profile names them?
5. Should generated models include Markdown bodies and concept IDs in a wrapper
   model or as excluded Pydantic fields?

## Decision

Proposed. Implementation should begin with the contract graph and explicit
profiles. Statistical inference from arbitrary bundles follows only after the
deterministic, reviewable path is stable.
