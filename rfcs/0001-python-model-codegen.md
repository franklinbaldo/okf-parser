---
type: RFC
title: Pydantic source generation from shared OKF schema contracts
description: Add deterministic Pydantic source as another schema target, reusing TypeContract and RFC 0006 declared DuckDB types instead of introducing a second contract graph
status: proposed
---

# RFC 0001: Pydantic source generation

## Summary

Add deterministic Pydantic v2 source generation as another output of the
existing schema pipeline:

```text
OKF documents ─────────────┐
                          ├─> TypeContract ─┬─> JSON Schema
.schema.sql declarations ─┘                 ├─> Zod
                                            ├─> dynamic Pydantic models
                                            └─> Pydantic source
```

The first implementation adds:

```bash
okf-parser schema ./knowledge --format pydantic
```

The command prints importable Python source to stdout. It uses the same
`TypeContract` objects, declared DuckDB logical types, naming rules,
requiredness, nullability, list structure and explicit casts already used by
the JSON Schema, Zod and dynamic-Pydantic exporters.

This RFC deliberately no longer proposes a second `ContractGraph`, a new
`codegen` command family, a machine-readable `OKFProfile.fields` extension, or
bidirectional Python-to-OKF generation in the first delivery.

Python-to-OKF generation remains interesting, but RFC 0006 changed the design
question materially: a Python-first exporter now has to decide whether its
canonical output is `.schema.sql`, JSON Schema, both, or another declaration
surface. That deserves a separate RFC rather than being coupled to Python
source generation.

## Context

The original RFC predates several pieces that now exist in `main`:

- `schema_contract.TypeContract` is already the language-neutral contract IR;
- `ScalarNode`, `ListNode`, `ObjectNode`, `LiteralNode` and `AnyNode` already
  describe the target-independent shape;
- RFC 0006 preserves exact declared DuckDB logical types in that contract;
- JSON Schema and Zod already compile from the same contract;
- `build_pydantic_models()` already creates runtime Pydantic models from the
  same contract;
- deterministic model naming and collision detection already exist;
- the CLI and MCP already expose `schema` as the schema-export surface.

Recreating those concepts as a separate contract graph would add a second type
system and a second source of truth precisely after the repository converged on
one shared schema pipeline.

## Goals

- expose deterministic, importable Pydantic v2 source for every compiled
  `TypeContract`;
- keep JSON Schema, Zod, dynamic Pydantic and generated Pydantic source on one
  semantic contract;
- preserve RFC 0006 target-specific mappings such as `Decimal`, `UUID`, lists,
  `TIMESTAMP` and `TIMESTAMPTZ` without reducing them back to a coarse cast
  family;
- preserve authored YAML field names even when they are not safe Pydantic/Python
  attribute names;
- keep producer extensions accepted by default;
- make output byte-for-byte deterministic for identical input and options;
- make the target available through the existing CLI/service/MCP `schema`
  surface without adding a second command hierarchy.

## Non-goals

- generating OKF declarations from Python models in this RFC;
- importing or executing Python source;
- parsing Python ASTs or Pydantic runtime metadata;
- introducing a second contract graph or separate type lattice;
- adding Ibis tables or NetworkX graphs for schema contracts;
- creating an `OKFProfile` concept type or a `fields` frontmatter extension;
- serializing validators, methods, computed fields or arbitrary Python code;
- guaranteeing a reversible mapping for every DuckDB logical type;
- writing generated files, managing generation markers, or implementing a
  dedicated `--check` mode in the first delivery.

The last item is intentional. A stdout-only deterministic exporter composes
with ordinary shell and CI tools:

```bash
okf-parser schema knowledge --format pydantic > generated_models.py
```

and drift checks can compare that output without teaching `okf-parser` a second
file-writing subsystem.

## Source of truth and precedence

Pydantic generation consumes `build_schema_contracts()` exactly as the current
schema targets do.

The existing precedence remains unchanged:

1. an explicit CLI `--cast` for a field wins;
2. otherwise a matching RFC 0006 `.schema.sql` declaration supplies the
   declared logical type;
3. otherwise observed values use the existing string-first behavior, or the
   existing `--infer-types` behavior when explicitly enabled.

The Pydantic target must not independently inspect YAML values after the
contract has been compiled.

That boundary is important. If JSON Schema, Zod and Pydantic ever disagree,
the disagreement must be a target projection policy, not a hidden second
inference engine.

## Interface

### CLI

Extend the existing schema format enum:

```bash
okf-parser schema ./knowledge --format pydantic

okf-parser schema ./knowledge \
  --format pydantic \
  --spec-template 'docs/types/{slug}.md'

okf-parser schema ./knowledge \
  --format pydantic \
  --infer-types
```

All existing schema flags keep their meaning.

The result is plain Python source, just as the Zod target returns plain source.
No JSON envelope is added around generated code.

### Python API

Add one renderer/exporter beside the existing schema exporters:

```python
from okf_parser.schema_export import export_pydantic_source

source = export_pydantic_source(
    "knowledge",
    spec_template="docs/types/{slug}.md",
)
```

The existing `build_pydantic_models()` remains useful for runtime validation and
must continue to compile from the same contracts.

### MCP

The existing `schema` MCP tool gains `pydantic` as another legal format through
the same public argument. No new MCP tool is required.

RFC 0008 annotations do not change merely because another pure renderer is
added. The tool's maximum effects are still governed by whether a legal
`spec_template` invocation can execute trusted declaration SQL.

## Generated module contract

The generated module is ordinary Pydantic v2 source and should require only
Python standard-library imports plus Pydantic.

A representative module is:

```python
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RegistroConcept(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["Registro"]
    valor: Decimal
    id_externo: UUID
```

### Extra fields

Generated top-level concept models use:

```python
ConfigDict(extra="allow")
```

OKF v0.2 permits producer-defined frontmatter extensions. Generated models are
therefore application adapters, not a mechanism for narrowing normative OKF
conformance.

Nested object models may use the same policy initially. A later RFC may add a
stricter target option if a consumer explicitly wants closed application
models.

The implementation should align `build_pydantic_models()` with the same
configuration so runtime-generated and source-generated models do not disagree
about producer extensions.

### Requiredness and nullability

The renderer preserves the existing independent contract dimensions:

- required, non-nullable: `field: T`;
- required, nullable: `field: T | None`;
- optional, non-nullable: `field: T = Field(default=None)`;
- optional, nullable: `field: T | None = None`.

The apparently unusual third form is deliberate. Pydantic v2 accepts omission
because the field has a default, while an explicitly supplied `None` is still
rejected because the annotation remains `T`. Widening that annotation to
`T | None` would collapse the contract's distinction between absence and an
authored null.

The current dynamic adapter already represents optional non-nullable fields as
annotation `T` with default `None`. The source renderer must stay semantically
aligned with that behavior rather than making its Python spelling superficially
more conventional but less precise.

A future contract representation may model the default value itself as a
separate dimension. That change belongs in the shared contract, not only in the
Pydantic renderer.

### Concept type discriminator

The authored `type` field remains a literal discriminator:

```python
type: Literal["Customer"]
```

The literal value is the exact authored concept type, independent of the
normalized Python class name.

## Field names and aliases

YAML keys are not constrained to safe Pydantic/Python attribute names. A source
generator must therefore separate the authored key from the Python attribute
name.

A field can use its authored spelling directly only when all of the following
hold:

- it is a valid Python identifier;
- it is not a Python keyword;
- it does not begin with `_`; Pydantic v2 treats leading-underscore names as
  private attributes rather than model fields;
- it does not collide with configuration or protected names used by
  `BaseModel`, such as `model_config` or `model_dump`;
- it does not collide with another generated attribute in the same model.

Otherwise the renderer generates a deterministic safe identifier and preserves
the authored key with `Field(alias=...)`:

```yaml
customer-id: abc
class: retail
model_dump: custom
_private: secret
__custom__: value
__pydantic_extra__: authored
```

may become:

```python
customer_id: str = Field(alias="customer-id")
class_: str = Field(alias="class")
model_dump_: str = Field(alias="model_dump")
private_: str = Field(alias="_private")
custom_: str = Field(alias="__custom__")
pydantic_extra_: str = Field(alias="__pydantic_extra__")
```

The same mapping helper must also be used by `build_pydantic_models()`. Dynamic
Pydantic models can otherwise fail or warn for authored keys that happen to
shadow `BaseModel` members even though those keys are legal OKF frontmatter.
Leading-underscore keys are included in this rule even when Python itself accepts
them as identifiers: `_private`, `__custom__` and `__pydantic_extra__` must remain
real validated fields through aliases, never disappear into Pydantic private
attributes or unvalidated extras.

Rules for safe identifiers must be deterministic and collision checked within a
model. Two distinct authored keys must never silently map to the same Python
attribute.

When aliases are present, generated models must validate authored keys through
the alias. The exact Pydantic configuration should be the smallest
configuration required for that behavior and must be pinned by tests.

## Target type projection

The renderer consumes the existing node tree and exact RFC 0006 declared types.
It does not create a new lattice.

### Scalars

The initial target mapping is:

| Contract / declared family | Python annotation |
| --- | --- |
| string | `str` |
| boolean | `bool` |
| integer | `int` |
| float | `float` |
| decimal | `Decimal` |
| date | `date` |
| timestamp | `datetime` |
| timestamptz | `datetime` |
| uuid | `UUID` |
| unsupported | `Any` |

The distinction between `TIMESTAMP` and `TIMESTAMPTZ` remains available in the
shared contract even though both project to `datetime` in this target. The
renderer must not erase that distinction from the IR itself.

### Lists

`ListNode` projects recursively:

```python
list[int]
list[UUID]
list[T | None]
```

A declared DuckDB array therefore follows the same element mapping as its
scalar family.

### Nested objects

`ObjectNode` produces a named nested Pydantic model. Nested names may continue to
derive from the owning model and field path using the existing deterministic
naming convention, but normalization alone is not an identity guarantee. Distinct
structural paths can collapse to the same class name.

Every Pydantic projection therefore maintains one registry for all emitted model
names in a compilation. Each entry records the generated class name and its full
structural path, represented as the top-level concept type plus authored field
segments. Registering the same generated name for a different structural path is
a hard `SchemaNameCollisionError` that reports both paths.

For example, paths equivalent to `a.structure_b` and `a_structure.b` must never
be allowed to produce two source classes with the same Python name and then rely
on postponed annotations to resolve whichever definition happens to come last.
The source renderer and `build_pydantic_models()` use the same registry even when
the dynamic model implementation could technically keep separate class objects;
this keeps both Pydantic projections on one observable collision contract.

Definitions are emitted before the models that reference them, or forward
references are used deterministically. The implementation should prefer the
simpler strategy supported by the actual contract tree produced today rather
than introducing a general dependency graph preemptively.

### Literals

`LiteralNode` projects to `Literal[value]`.

### Unknown/unsupported values

`AnyNode` and declared families that have no honest Pydantic representation
project to `Any`.

This is a target limitation, not a loss of declaration identity: JSON Schema,
DuckDB materialization or another future target may still retain richer
information from the same `TypeContract`.

## Imports

Imports are derived from the annotations and field definitions actually emitted
and sorted canonically.

Potential imports include:

```python
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
```

Unused imports must not be emitted. Generated modules must pass the repository's
Ruff rules without requiring `noqa` or formatting after generation.

## Determinism

For identical bundle contents and options, output must be byte-for-byte stable.
At minimum, determinism covers:

- concept model order;
- nested model order;
- field order inherited from `TypeContract`;
- import order;
- generated attribute names and aliases;
- blank lines and final newline;
- string quoting;
- union spelling.

The renderer itself owns canonical formatting. It should not invoke Ruff as a
subprocess merely to stabilize output.

## Relationship to dynamic Pydantic models

`build_pydantic_models()` and generated source are two projections of the same
contract and should be tested for semantic equivalence across the supported
subset.

The implementation may refactor the dynamic adapter to share Pydantic-specific
field-name mapping and configuration helpers with the source renderer. Those
helpers are target projection policy; they do not belong in `TypeContract`
unless another target later demonstrates the same requirement.

Examples of equivalence include:

- the same authored field names/aliases;
- the same requiredness and explicit-null behavior;
- `Decimal` and `UUID` runtime annotations;
- list element types and nullability;
- literal `type` discrimination;
- acceptance of unknown producer fields;
- safe handling of authored keys that start with `_` or shadow Python keywords
  or `BaseModel` members;
- identical hard failures for nested generated-model name collisions, including
  both structural paths in the error.

The source generator must not call `model_json_schema()` on the dynamic models
and then reverse-engineer Python from JSON Schema. Both paths should consume the
shared contract directly.

## Why not a new ContractGraph

The original proposal described an immutable graph with fields, references,
diagnostics and target metadata. The repository now has the useful subset in
`TypeContract` and its node classes.

Adding another IR would create questions with no product benefit:

- which IR owns declared DuckDB precision and array element types;
- which IR owns requiredness/nullability;
- whether JSON Schema/Zod consume the old or new graph;
- how two inference implementations stay synchronized;
- whether Ibis/NetworkX representations are authoritative or derived.

The answer in this RFC is simpler: extend the shared contract only when a real
cross-target requirement appears.

## Why not `codegen pydantic`

`schema` already means "compile the frontmatter contract into another schema or
validation language" and already returns source for Zod.

Pydantic source is the same kind of operation. A new top-level `codegen`
hierarchy would duplicate:

- input discovery;
- `--exclude`;
- `--infer-types`;
- `--cast`;
- `--spec-template`;
- service wiring;
- MCP wiring;
- documentation.

If a later feature needs project-aware multi-file generation, import management
or filesystem ownership, it may justify a separate codegen surface. A single
stdout module does not.

## Why Python-to-OKF is deferred

The original RFC coupled source generation with the reverse direction:

```text
Python annotations -> OKF profile bundle
```

RFC 0006 changed what an explicit OKF-side declaration means. `.schema.sql` can
now carry exact DuckDB types, comments and executable trusted SQL, while JSON
Schema is a target representation rather than the canonical declaration input.

A reverse generator therefore needs separate decisions about:

- whether Python annotations map to `.schema.sql`, JSON Schema or both;
- how `Decimal` precision/scale is obtained when Python's annotation omits it;
- whether `datetime` means DuckDB `TIMESTAMP` or `TIMESTAMPTZ`;
- how Pydantic constraints map to DuckDB versus JSON Schema;
- whether runtime Pydantic metadata may be imported or only AST-inspected;
- what authority generated files have relative to handwritten declarations.

Those are not required to generate Pydantic source from an existing
`TypeContract`. Coupling them would delay the smaller useful feature and
reintroduce a second contract model.

## Diagnostics and errors

The first implementation reuses existing schema compilation failures:

- top-level and nested generated-model name collisions remain
  `SchemaNameCollisionError`, with both owning structural paths reported;
- invalid explicit casts remain `SchemaCastError`;
- invalid declared schemas remain `SchemaExportError`;
- unsupported declared types render as `Any` rather than inventing a fake type.

Source-specific failures should be added only for conditions the renderer
cannot represent honestly, such as two authored keys colliding after safe
Pydantic/Python attribute generation. Nested class-name collisions are not
source-only: the shared Pydantic naming registry makes them the same hard failure
for dynamic and source-generated models.

A separate family of `GEN00x` diagnostics is not introduced until there are
multiple generation operations that need aggregate diagnostics.

## Implementation plan

1. Add a deterministic Pydantic field-name/alias helper with collisions,
   leading-underscore keys, keywords and `BaseModel` protected-name tests.
2. Add a compilation-wide Pydantic model-name registry keyed by generated class
   name and carrying full structural-path provenance; fail on any distinct path
   collision.
3. Refactor `build_pydantic_models()` to use the same field mapping, model-name
   registry and `extra="allow"` policy.
4. Add a pure `render_pydantic_source(contracts)` renderer beside `render_zod`.
5. Add `export_pydantic_source()` beside the existing schema exporters.
6. Add `pydantic` to the schema format accepted by service, CLI and MCP.
7. Pin semantic equivalence against `build_pydantic_models()` for representative
   contracts, including optional-non-nullable fields.
8. Add declared-type regressions for `DECIMAL`, `UUID`, timestamp families and
   arrays.
9. Add regressions for `_private`, `__custom__`, `__pydantic_extra__`, keywords,
   Unicode, `BaseModel` members and distinct nested paths that normalize to the
   same generated class name.
10. Document stdout/file-redirection and CI drift-check examples.

## Acceptance criteria

- repeated generation is byte-for-byte deterministic;
- generated source imports successfully on supported Python versions;
- generated source passes Ruff format/check without post-processing;
- top-level generated models accept and preserve unknown frontmatter fields;
- authored field names survive through aliases where Python/Pydantic attribute
  names differ;
- alias normalization collisions fail loudly;
- authored keys beginning with `_`, including `_private`, `__custom__` and
  `__pydantic_extra__`, remain validated fields via aliases in both dynamic and
  source-generated models;
- every emitted Pydantic class name is unique across the compilation, and a
  collision between distinct structural paths fails loudly while reporting both
  paths;
- optional non-nullable fields accept omission but reject explicit `null`;
- JSON Schema, Zod, dynamic Pydantic and Pydantic source all compile from the
  same `TypeContract` objects;
- dynamic and source Pydantic projections agree on field aliases, nullability,
  producer extensions and protected-name handling;
- declared `DECIMAL`, UUID, timestamp and array families use the expected
  target-specific Python annotations;
- unsupported declared families become `Any` without discarding their identity
  from the shared contract;
- `schema --format pydantic` is available through CLI and the existing MCP
  `schema` tool;
- no new contract graph, profile format, Python import mode or filesystem writer
  is introduced.

## Follow-up work

A separate RFC may address Python-to-OKF declaration generation. It should begin
from the post-RFC-0006 question of declaration authority and target format, not
from the old assumption that an `OKFProfile.fields` frontmatter extension is the
canonical interchange.

A later codegen/project RFC may also add owned output files, multi-module
splitting and check/write modes if real consumers need more than deterministic
stdout.

## Decision

Proposed.

The implementation should begin with the Pydantic-specific field mapping and
source renderer over the existing `TypeContract`. No new intermediate
representation is required.
