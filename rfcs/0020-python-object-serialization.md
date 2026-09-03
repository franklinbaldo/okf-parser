---
type: RFC
title: Python object-to-OKF representation protocol
status: proposed
description: Define how Python and JSON-like objects project metadata plus an optional body into a typed canonical OKF document
---

# RFC 0020: Python object-to-OKF representation protocol

## Summary

`okf-parser` should treat Python serialization as **representation**, not as a domain object manually
constructing an already-finished OKF document.

A representation has two semantic channels:

1. **metadata** — values that become YAML frontmatter;
2. **body** — optional Markdown/text chosen by the producer.

The final OKF document always has a non-empty `type`. The serializer guarantees that invariant. For
ordinary Python objects the default type is the source class name; producers and callers may override
it explicitly.

```python
from dataclasses import dataclass

from okf_parser import OKFRepresentation, dumps

@dataclass
class Processo:
    numero: str
    texto: str

    def __okf__(self) -> OKFRepresentation:
        return OKFRepresentation(
            metadata={"numero": self.numero},
            body=self.texto,
        )

text = dumps(Processo("0001", "# Processo\n"))
```

The resulting frontmatter begins with `type: Processo`; the producer did not need to repeat its class
name.

This RFC resolves #233. It is distinct from #67: #67 projects already-parsed OKF state outward as
application data; this RFC projects application data inward into OKF.

## Decision

### 1. Define a library protocol with `__okf__()`

Custom Python objects opt in structurally:

```python
from typing import Protocol

class SupportsOKF(Protocol):
    def __okf__(self) -> OKFRepresentation | OKFDocument | Mapping[str, object]: ...
```

`__okf__` is not a Python interpreter special method. It is an OKF ecosystem protocol, analogous in
purpose to library-defined protocols such as Rich's `__rich__`: a recognizable hook that says the
object knows how it should be represented by this ecosystem.

No inheritance or registration is required. Runtime dispatch remains ordinary duck typing: obtain
`__okf__`, ensure it is callable, invoke it exactly once, then validate the result.

### 2. Separate producer representation from final document

The producer-facing value is:

```python
@dataclass(frozen=True, slots=True)
class OKFRepresentation:
    metadata: Mapping[str, object]
    body: str = ""
```

`metadata` is semantic data destined for YAML frontmatter. `body` is optional content destined for the
OKF body. The producer chooses the split.

The parser-facing value remains:

```python
@dataclass(frozen=True, slots=True)
class OKFDocument:
    frontmatter: Mapping[str, YamlValue]
    body: str = ""
```

`OKFDocument` is stricter because it represents a complete concept: its frontmatter must already have
a valid `type` and values in the parser's spelling-preserving `YamlValue` domain.

This distinction is intentional:

```text
Python/JSON object
    -> OKFRepresentation(metadata + optional body)
    -> type resolution + scalar normalization
    -> OKFDocument(required typed frontmatter + body)
    -> canonical Markdown
```

### 3. The serializer owns the required `type`

OKF requires `type` in final frontmatter. Producers should not have to repeat boilerplate when the
natural type is already represented by their Python class.

Type resolution precedence is:

1. explicit `concept_type=` argument supplied by the caller;
2. a `type` field explicitly present in representation metadata;
3. the source object's class name, when there is a source class to infer from;
4. otherwise fail because a type cannot be invented safely.

Examples:

```python
class Pessoa:
    def __okf__(self):
        return {"nome": self.nome}

# -> type: Pessoa

dumps(Pessoa(...), concept_type="Person")
# -> type: Person

class LegacyPessoa:
    def __okf__(self):
        return {"type": "Pessoa", "nome": self.nome}

# -> type: Pessoa
```

A bare mapping has no meaningful domain class: inferring `dict` would describe the Python container,
not the concept. Therefore it must either contain `type` or be serialized with `concept_type=`.

### 4. Support JSON-like mappings directly

A JSON object represented as a Python mapping is already a natural metadata projection:

```python
dumps(
    {"title": "Example", "count": 3, "active": True},
    concept_type="Example",
)
```

The mapping becomes frontmatter metadata and the body is empty.

This path does not reflect over arbitrary Python objects. Only explicit supported shapes are accepted.

### 5. Support Pydantic as a first-class default projection

Pydantic is already a dependency and provides an explicit public serialization API. A `BaseModel`
without `__okf__` is projected with:

```python
model.model_dump(mode="json")
```

All fields become metadata, body is empty, and the model class name is the default OKF type.

A Pydantic model that wants a different YAML/body split implements `__okf__`; the explicit protocol
wins over the default Pydantic projection.

```python
class Article(BaseModel):
    title: str
    text: str

    def __okf__(self):
        return OKFRepresentation(
            metadata={"title": self.title},
            body=self.text,
        )
```

The same principle applies to ordinary classes: the serializer does not inspect `__dict__`; they opt
in with `__okf__`.

### 6. `__okf__()` may use a compact or explicit representation

For metadata-only objects, returning a mapping is enough:

```python
def __okf__(self):
    return {"title": self.title}
```

This is equivalent to `OKFRepresentation(metadata=..., body="")`.

When body placement matters, return `OKFRepresentation` explicitly. Advanced producers may return an
already-valid `OKFDocument`, although that opts out of type inference unless the caller deliberately
overrides its type.

Raw Markdown is not an accepted hook result because it would move YAML quoting, delimiters and
canonicalization into every producer.

### 7. Normalize JSON scalars into OKF's spelling-preserving domain

The parser intentionally represents YAML scalar spellings as strings (plus `None`) so values such as
`0012`, `false` and `null` do not silently change semantic identity.

Object conversion therefore accepts ordinary JSON scalar values and canonicalizes them before
constructing `OKFDocument`:

- strings remain strings;
- `None` remains `None`;
- booleans become `"true"` or `"false"`;
- integers become their decimal spelling;
- finite floats become their JSON numeric spelling;
- mappings recurse and require string keys;
- lists/tuples recurse and preserve order;
- arbitrary nested Python objects are rejected.

Non-finite floats are rejected because they are not portable JSON numbers.

This makes Pydantic/JSON conversion ergonomic without weakening the parser's existing scalar model.

### 8. Normalize with `to_okf(value, *, concept_type=None)`

The public conversion primitive returns a complete `OKFDocument`:

```python
def to_okf(value: object, *, concept_type: str | None = None) -> OKFDocument: ...
```

Supported inputs are:

1. `OKFDocument`;
2. `OKFRepresentation`;
3. string-keyed mappings containing JSON-like values;
4. objects with callable `__okf__()`;
5. Pydantic `BaseModel` values.

For objects that are both Pydantic and implement `__okf__`, the explicit hook wins.

There is no `__dict__`, `vars()`, arbitrary dataclass reflection or recursive object-graph fallback.

### 9. Render with `dumps(value, *, concept_type=None)`

`dumps()` calls `to_okf()` and renders one deterministic OKF Markdown string. It performs no
filesystem writes.

New documents use the repository's existing canonical top-level physical order:

```text
type
title         # when present
description   # when present
<all remaining keys lexicographically>
```

Nested mappings are sorted lexicographically and list order is preserved.

This is a physical determinism rule, not semantic ordering.

### 10. Representation does not prescribe what belongs in body

The protocol deliberately does **not** decide that a field such as `description`, `text`, `content`,
`prompt` or `source` must live in YAML or body.

That is a producer decision. Two valid representations of the same application object may choose
different splits for different consumers.

For example:

```python
# metadata-heavy
OKFRepresentation(
    metadata={"title": article.title, "text": article.text},
)

# body-heavy
OKFRepresentation(
    metadata={"title": article.title},
    body=article.text,
)
```

Both are legitimate projections so long as the resulting concept satisfies the relevant OKF type
contract.

### 11. New-document rendering and edit rendering stay separate

`dumps()` creates canonical new text. Its round-trip guarantee is semantic:

```text
object
  -> representation
  -> OKFDocument
  -> dumps()
  -> parse_document_text()
  -> equivalent frontmatter and body
```

It is not a byte-preserving edit API. Existing edit/apply paths continue to preserve authored quotes,
comments, BOMs and line endings.

### 12. The Python protocol is local; OKF output remains portable

`__okf__` and Pydantic support are Python integration conveniences. TypeScript and Rust do not need
methods with identical spelling. The emitted OKF stays portable and obeys the same format semantics
across runtimes.

## Public API

RFC 0020 exports:

```python
OKFRepresentation
OKFDocument
SupportsOKF
to_okf
dumps
```

Typical custom class:

```python
class Processo:
    def __okf__(self):
        return OKFRepresentation(
            metadata={"numero": self.numero, "assunto": self.assunto},
            body=self.texto,
        )
```

Typical Pydantic model needing no custom body:

```python
class Pessoa(BaseModel):
    nome: str
    idade: int

text = dumps(Pessoa(nome="Ana", idade=42))
```

Typical JSON-like object:

```python
text = dumps({"nome": "Ana", "idade": 42}, concept_type="Pessoa")
```

## Error model

| Situation | Result |
| --- | --- |
| native valid `OKFDocument` | returned directly |
| plain unsupported object | `TypeError` |
| non-callable `__okf__` on plain object | `TypeError` |
| hook returns unsupported shape | `TypeError` |
| producer hook raises | original exception propagates |
| bare mapping/representation has no resolvable type | `TypeError` |
| declared type is non-string | `TypeError` |
| resolved type is blank | `ValueError` |
| metadata mapping has non-string key | `TypeError` |
| metadata contains arbitrary nested object | `TypeError` |
| metadata contains non-finite float | `TypeError` |
| representation body is non-string | `TypeError` |

## Testing

The implementation must cover:

1. native `OKFDocument` pass-through;
2. bare mapping type requirement and explicit caller type;
3. structural `__okf__` dispatch without inheritance;
4. class-name type inference;
5. metadata-declared type override;
6. caller type override;
7. exactly one hook invocation;
8. Pydantic default projection;
9. Pydantic custom YAML/body split via `__okf__`;
10. JSON scalar normalization including bool/int/float/list/map;
11. invalid hook results and exception propagation;
12. invalid representation envelopes;
13. non-string keys, arbitrary objects and non-finite floats;
14. deterministic canonical ordering;
15. YAML-sensitive strings and Unicode round-trip;
16. body round-trip.

## Alternatives considered

### `to_okf()` as the producer hook

Rejected. The protocol is intended to advertise an ecosystem capability rather than an ordinary
business-method conversion. A library-defined `__okf__` makes that role explicit and follows an
established Python library pattern for structural rendering/representation hooks.

### Requiring every producer to construct `OKFDocument`

Rejected. It forces every object to repeat the required `type` and conflates producer representation
with the parser's final normalized document.

### Inferring `dict` as the type of a JSON object

Rejected. `dict` identifies the implementation container, not the domain concept.

### Automatic `__dict__` or arbitrary dataclass reflection

Rejected. Private implementation fields must not leak into OKF by accident. Explicit `__okf__` is the
extension point for ordinary classes. Pydantic is handled separately because it exposes a deliberate
public serialization API.

### Hook returns raw Markdown

Rejected because syntax, YAML quoting, delimiters and canonicalization belong to the serializer.

### External adapter registry / `singledispatch`

Deferred. It can later adapt third-party classes that cannot implement `__okf__` without changing the
core representation contract.

## Non-goals

RFC 0020 does not define deserialization into arbitrary Python classes, bundle/object-graph
serialization, implicit arbitrary-object reflection, byte-preserving edits, filesystem writes, or a
cross-language `__okf__` method.

## Consequences

The boundary becomes:

```text
application object      chooses semantic data and optional body split
OKFRepresentation       carries metadata + body before required type resolution
okf-parser conversion   resolves type and normalizes JSON-like values
OKFDocument             carries one validated typed concept
canonical renderer      owns physical OKF Markdown output
```

This makes OKF usable as a real representation target for Python objects, Pydantic models and
JSON-like data while preserving the format's required `type`, parser semantics and deterministic
output.
