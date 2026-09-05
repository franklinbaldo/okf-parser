---
type: Documentation
title: Python object serialization
description: Represent Python, Pydantic, and JSON-like objects as typed OKF metadata plus an optional body
---

# Python object serialization

`okf-parser` treats serialization as an object-to-OKF **representation**. A producer supplies metadata
for YAML frontmatter and, when useful, a body. The serializer resolves the required OKF `type`,
normalizes JSON-like values, and owns the physical Markdown/YAML rendering.

## Custom Python objects

A class opts into the OKF protocol with `__okf__()`:

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

The output is canonical OKF text. The class supplies semantic data; the serializer owns `type`, YAML
quoting and delimiters:

```markdown
---
type: Processo
numero: '0001'
---
# Processo
```

The type is inferred as `Processo`. A metadata-only producer may return a mapping directly:

```python
class Pessoa:
    def __okf__(self):
        return {"nome": self.nome}
```

A producer can override the inferred class type by returning `{"type": "Person", ...}`. A caller can
override either form with `concept_type=`.

Protocol dispatch is strict. `__okf__` is resolved once and the resolved callable is invoked once. If
an object explicitly exposes `__okf__` but that attribute is not callable, serialization raises
`TypeError` instead of silently choosing another representation path.

## Pydantic

Pydantic models work without a custom hook. Their `model_dump(mode="json")` representation becomes
frontmatter metadata, their class name becomes the default type, and the body is empty:

```python
from pydantic import BaseModel
from okf_parser import dumps

class Pessoa(BaseModel):
    nome: str
    idade: int

text = dumps(Pessoa(nome="Ana", idade=42))
```

If a model wants some fields in the body instead, it implements `__okf__()` just like any other class:

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

The explicit protocol wins over the default Pydantic projection. A present but non-callable `__okf__`
is treated as an invalid explicit protocol declaration and does not fall back to `model_dump()`.

## JSON-like objects

A Python mapping containing JSON-like values is treated as metadata. Since a bare `dict` has no useful
domain class name, it must contain `type` or receive `concept_type=`:

```python
text = dumps(
    {"nome": "Ana", "idade": 42, "active": True},
    concept_type="Pessoa",
)
```

JSON scalars are normalized into OKF's spelling-preserving scalar domain: booleans become `"true"` or
`"false"`, integers and finite floats become their canonical textual spelling, strings stay strings,
and `None` stays `None`. Nested mappings and lists recurse. Arbitrary nested Python objects and
non-finite floats are rejected.

## Public API

- `OKFRepresentation(metadata, body="")` — producer-facing semantic projection before type resolution;
- `OKFDocument(frontmatter, body="")` — complete validated OKF document with a required type;
- `SupportsOKF` — structural typing protocol for `__okf__()` producers;
- `to_okf(value, concept_type=None)` — convert a supported Python value into `OKFDocument`;
- `dumps(value, concept_type=None)` — render deterministic OKF Markdown.

Type resolution is: caller `concept_type=` first, then metadata `type`, then the source object's class
name. A bare mapping or standalone `OKFRepresentation` without either an explicit type or a caller
override is rejected rather than becoming `type: dict`.

Precedence only selects among valid candidates. Whenever present, both caller `concept_type=` and a
metadata `type` must be non-empty strings. A malformed metadata `type` is rejected even when the caller
also supplies a valid override, so broken input cannot be hidden accidentally.

The producer decides what belongs in metadata and what belongs in body. `okf-parser` does not impose a
policy such as forcing `text`, `description`, or `content` into one channel.

The renderer keeps the repository's canonical physical order for new documents: `type`, `title`, and
`description` first when present, followed by remaining keys lexically. Existing authored-document
edits continue through the style-preserving edit/apply paths rather than canonical serialization.
