---
type: RFC
title: Pydantic source generation from shared OKF schema contracts
description: Generate deterministic Pydantic source from TypeContract and RFC 0006 declared DuckDB types without a second contract graph
status: accepted
---

# RFC 0001: Pydantic source generation

## Summary

`okf-parser` exposes deterministic Pydantic v2 source as another output of the
existing schema pipeline:

```text
OKF documents ─────────────┐
                          ├─> TypeContract ─┬─> JSON Schema
.schema.sql declarations ─┘                 ├─> Zod
                                            ├─> dynamic Pydantic models
                                            └─> Pydantic source
```

The public interface is intentionally small:

```bash
okf-parser schema . --format pydantic
```

It prints one importable Python module to stdout. The Pydantic target uses the
same `TypeContract`, declared DuckDB logical types, requiredness, nullability,
list structure and explicit casts as the existing schema targets.

This RFC does **not** introduce a second `ContractGraph`, a `codegen` command
family, an `OKFProfile.fields` format or Python-to-OKF generation.

## Usability contract

The common path must stay boring:

```bash
okf-parser schema . --format pydantic
```

An ordinary conformant bundle requires no alias configuration, naming policy,
profile scaffolding or additional setup. Safe Pydantic names, aliases for
authored keys, protected namespaces and nested model-name registration are
compiler responsibilities.

Advanced controls remain progressive disclosure:

- `--infer-types` opts into observed scalar inference;
- `--cast` supplies an explicit scalar override;
- `--spec-template` reads RFC 0006 `.schema.sql` declarations when the caller
  wants declared physical types.

When automatic naming is genuinely ambiguous, generation fails with the
authored keys or structural paths involved. The user is not required to
preconfigure a naming map.

This principle applies beyond this target: internal architectural complexity is
not itself a reason to add a public command, config file or setup step.

## Source of truth

Pydantic generation consumes `build_schema_contracts()` exactly as JSON Schema
and Zod do.

Precedence remains:

1. explicit `--cast`;
2. matching RFC 0006 `.schema.sql` declared type;
3. otherwise the existing string-first behavior, or `--infer-types` when
   explicitly enabled.

The Pydantic projection does not inspect authored YAML again after
`TypeContract` is compiled. Target disagreements therefore remain projection
policy, not hidden inference engines.

## Interfaces

### CLI

```bash
okf-parser schema ./knowledge --format pydantic

okf-parser schema ./knowledge \
  --format pydantic \
  --spec-template 'docs/types/{slug}.md'
```

The result is plain Python source, like the Zod target. Shell redirection is the
file-writing mechanism for this delivery:

```bash
okf-parser schema ./knowledge --format pydantic > generated_models.py
```

There is no Pydantic-specific writer or `--check` subsystem.

### Python

```python
from okf_parser.schema_export import export_pydantic_source

source = export_pydantic_source(
    "knowledge",
    spec_template="docs/types/{slug}.md",
)
```

`build_pydantic_models()` remains the runtime-model projection and shares the
same Pydantic naming/configuration policy.

### MCP

The existing `schema` tool accepts `pydantic` through its existing `format`
argument. No new MCP tool is added.

RFC 0008 effect annotations are unchanged: the tool's maximum effects still
account for a supplied `spec_template` executing trusted declaration SQL.

## Generated module contract

The generated module requires only standard-library imports plus Pydantic.
Imports are emitted only when used and in deterministic order.

A representative output is:

```python
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

Generated source deliberately uses immediately evaluated annotations rather
than `from __future__ import annotations`. Nested object classes are emitted
children-first, so imports and ordinary execution do not require a caller to run
`model_rebuild()` merely to resolve the generator's own names.

## Producer extensions

Top-level generated concept models use:

```python
ConfigDict(extra="allow")
```

OKF permits producer-defined frontmatter fields, so generated models are
application adapters rather than a mechanism for narrowing OKF conformance.
Dynamic models use the same policy.

## Requiredness and nullability

Presence and nullability stay independent:

- required, non-nullable: `field: T`;
- required, nullable: `field: T | None`;
- optional, non-nullable: `field: T = Field(default=None)`;
- optional, nullable: `field: T | None = None`.

The third spelling is intentional. Pydantic accepts omission because a default
exists, while explicit `None` is rejected because the annotation remains `T`.
This preserves absence versus authored `null`.

Dynamic and source-generated models implement the same behavior.

## Authored field names and aliases

YAML keys are not restricted to safe Pydantic attributes. A key may use its
authored spelling directly only when it is all of the following:

- a valid Python identifier;
- not a Python keyword;
- not prefixed with `_`;
- not in Pydantic's protected/configuration namespace;
- not prefixed with `model_`;
- not colliding with another generated attribute in the same model.

Unsafe names receive deterministic internal attributes and retain the exact
authored key through `Field(alias=...)`.

For example:

```yaml
customer-id: abc
class: retail
model_dump: custom
_private: secret
__custom__: value
__pydantic_extra__: authored
```

is represented along the lines of:

```python
customer_id: str = Field(alias="customer-id")
class_: str = Field(alias="class")
field_model_dump: str = Field(alias="model_dump")
private_: str = Field(alias="_private")
custom_: str = Field(alias="__custom__")
pydantic_extra_: str = Field(alias="__pydantic_extra__")
```

All leading-underscore keys are alias-required even though Python accepts some
of them as identifiers. This prevents Pydantic from silently treating authored
fields as private attributes or unvalidated extras.

The `model_` prefix is also conservatively alias-required. This avoids warnings
and future ambiguity with Pydantic's protected namespace without asking the user
to configure `protected_namespaces`.

The same mapper is used by `build_pydantic_models()` and the source renderer.
Two authored keys that normalize to the same Pydantic attribute fail with
`SchemaNameCollisionError` rather than silently overwriting one another.

## Nested model names

Nested `ObjectNode` values become named Pydantic classes. Names are derived
deterministically from the owning model and authored field path.

Normalization is not treated as identity. Every Pydantic compilation keeps one
registry mapping generated class name to its full structural path. If a second,
different path claims the same generated class name, generation fails with
`SchemaNameCollisionError` and reports both paths.

This catches cases such as structurally distinct paths equivalent to:

```text
a.structure_b
a_structure.b
```

which can normalize to the same class name. Dynamic and source-generated
Pydantic models share this collision contract.

Nested source classes are emitted before their owners, so annotations can be
evaluated normally without a separate dependency graph or post-generation
rebuild step.

## Type projection

The Pydantic target maps the shared contract as follows:

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

`ListNode` projects recursively, including nullable elements. `LiteralNode`
projects to `Literal[value]`. `AnyNode` and declared types without an honest
Pydantic representation project to `Any`.

The fact that `TIMESTAMP` and `TIMESTAMPTZ` both project to `datetime` does not
erase their distinction from `TypeContract` or other targets.

## Determinism

Identical bundle contents and options produce byte-for-byte identical source.
Determinism includes:

- concept and nested-model order;
- field order inherited from `TypeContract`;
- import order;
- generated attributes and aliases;
- blank lines and final newline;
- quoting and union spelling.

The renderer owns its source formatting. It does not invoke Ruff as a subprocess
to make output deterministic.

## Errors

The target reuses existing schema errors:

- generated model-name collisions: `SchemaNameCollisionError`;
- generated field-name collisions: `SchemaNameCollisionError`;
- invalid explicit casts: `SchemaCastError`;
- invalid declared schemas: `SchemaExportError`.

Unsupported declared types project to `Any`; their exact DuckDB identity remains
available in the shared contract for other targets.

No separate `GEN00x` diagnostic family is introduced for this single renderer.

## Deferred work

Python-to-OKF generation is intentionally separate. After RFC 0006, a reverse
generator must decide independently:

- whether Python annotations should produce `.schema.sql`, JSON Schema or both;
- how `Decimal` precision/scale is supplied;
- whether Python `datetime` means `TIMESTAMP` or `TIMESTAMPTZ`;
- how Pydantic constraints map to DuckDB and schema targets;
- whether source is statically inspected or trusted runtime code is imported;
- what authority generated declarations have relative to handwritten files.

Those decisions are unnecessary for OKF-to-Pydantic source generation and must
not force a second contract model into this feature.

A later project-oriented codegen feature may add owned files, multiple modules
or dedicated drift checking if real consumers need more than deterministic
stdout.

## Acceptance criteria

The implementation is accepted when all of the following remain true:

- `okf-parser schema . --format pydantic` works without naming configuration or
  profile scaffolding for an ordinary conformant bundle;
- output is deterministic and importable;
- generated source passes the repository formatting/lint contract without
  post-processing;
- producer extensions remain accepted;
- optional non-nullable fields accept omission and reject explicit `null`;
- `_private`, `__custom__`, `__pydantic_extra__`, Python keywords and protected
  `model_*` names remain validated fields through automatic aliases;
- alias normalization collisions fail loudly;
- nested model-name collisions fail with both structural paths;
- dynamic and source-generated Pydantic projections share naming, aliases,
  nullability and extension policy;
- declared `DECIMAL`, UUID, timestamp and array families retain their expected
  target-specific mappings;
- CLI and MCP expose `pydantic` through the existing `schema` surface;
- no second contract graph, profile format, Python import mode or filesystem
  writer is introduced.

## Decision

Accepted in 0.20.0.

The implementation uses the existing `TypeContract` as the sole schema IR and a
shared Pydantic projection layer for both dynamic models and generated source.
The public UX remains one existing command with one additional format value.
