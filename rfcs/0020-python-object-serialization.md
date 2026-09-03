---
type: RFC
title: Python object-to-OKF serialization protocol
status: proposed
description: Define a structural Python protocol and canonical document value for converting application objects into deterministic OKF without inventing a custom dunder or leaking YAML syntax into domain models
---

# RFC 0020: Python object-to-OKF serialization protocol

## Summary

`okf-parser` should expose a small Python-native boundary for turning an application object into one
complete OKF document. A producer opts in structurally with an ordinary `to_okf()` method returning a
parser-owned `OKFDocument`; the parser validates and renders that value.

```python
from okf_parser import OKFDocument, dumps

class Pessoa:
    def to_okf(self) -> OKFDocument:
        return OKFDocument(
            frontmatter={"type": "Pessoa", "nome": self.nome},
            body=self.biografia,
        )

text = dumps(Pessoa(...))
```

The producer owns its semantic projection. `okf-parser` owns the OKF value contract, YAML quoting,
canonical physical ordering and Markdown envelope.

This RFC resolves #233. It is distinct from #67: #67 projects already-parsed OKF state outward as
JSON-ready application data; this RFC projects a Python object inward to an OKF document.

## Decision

### 1. Use `to_okf()`, not a package-defined `__okf__`

The motivating idea used `__okf__()`. The public protocol deliberately does not.

Python reserves names of the form `__*__` for system-defined behavior and warns that undocumented
uses may break as the interpreter evolves. Interpreter protocols such as `__fspath__` are safe
because Python itself standardizes them; a library-created dunder does not have that status.

The extension point is therefore an ordinary method:

```python
def to_okf(self) -> OKFDocument: ...
```

It retains duck-typing ergonomics without occupying Python's reserved dunder namespace.

### 2. Publish a static structural protocol

The package exposes:

```python
from typing import Protocol

class SupportsOKF(Protocol):
    def to_okf(self) -> OKFDocument: ...
```

No producer inherits from or registers with `SupportsOKF`. Implementing the method is sufficient for
static structural typing.

The protocol is intentionally not `@runtime_checkable`. Runtime-checkable protocols only verify
attribute presence, not the declared signature, and Python documents that their `isinstance()` checks
may be slower than ordinary attribute lookup. The serializer must call and validate the hook anyway.

### 3. `OKFDocument` is the semantic boundary

The parser owns a compact value:

```python
from collections.abc import Mapping
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class OKFDocument:
    frontmatter: Mapping[str, YamlValue]
    body: str = ""
```

It represents one complete concept document, not a path or a physical file. It contains no BOM,
newline policy, YAML emitter, destination, content hash or filesystem state.

Construction validates and copies the top-level mapping against the parser's existing recursive
`YamlValue` contract. The serializer does not widen OKF scalar semantics merely because Python has
integers, floats, booleans or arbitrary objects available.

### 4. Normalize with `to_okf(value)`

The public normalization primitive is:

```python
def to_okf(value: OKFDocument | SupportsOKF) -> OKFDocument: ...
```

Its runtime rules are deliberately small:

1. an `OKFDocument` passes through directly;
2. otherwise obtain `value.to_okf` structurally;
3. missing or non-callable hooks raise `TypeError`;
4. call the hook exactly once;
5. require an `OKFDocument` result, otherwise raise `TypeError`;
6. let exceptions from the producer hook propagate unchanged.

There is no fallback to `__dict__`, `dataclasses.asdict()`, Pydantic `model_dump()` or reflection.
Private Python representation must not become OKF accidentally.

### 5. Render with `dumps(value)`

```python
def dumps(value: OKFDocument | SupportsOKF) -> str: ...
```

`dumps()` normalizes first, then renders one deterministic OKF Markdown string. It has no filesystem
side effects. A `dump(value, fp)` convenience is deferred until a real consumer needs it.

The hook returns a semantic value, never raw Markdown. Otherwise every domain object would need to
know YAML quoting, delimiters, physical ordering and future renderer policy.

### 6. Validate before emission

A serializable document must satisfy:

- frontmatter is a string-keyed mapping accepted by `YamlValue`;
- `type` exists and is a non-empty string;
- `body` is a string.

Wrong Python shapes raise `TypeError`; an empty string `type` raises `ValueError`. Unsupported nested
objects are rejected rather than converted implicitly.

This means a producer cannot accidentally emit a document that the normal parser immediately rejects
for the same basic value-shape reasons.

### 7. Reuse the repository's canonical physical order

New documents have no authored ordering to preserve. Their top-level frontmatter therefore uses the
existing `frontmatter_key_order()` convention already owned by `frontmatter_order.py`:

```text
type
title         # when present
description   # when present
<all remaining keys lexicographically>
```

Nested mappings are ordered lexicographically and list order is preserved exactly.

This is a physical determinism rule, not semantic ordering. Equivalent mappings constructed with
different insertion orders must serialize to the same bytes.

The implementation must reuse `frontmatter_key_order()` rather than introduce a second canonical
ordering policy.

### 8. Do not recursively serialize object graphs

`to_okf()` means “represent this value as one complete OKF document.” It does not mean “walk every
nested Python object and call similarly named methods.”

```python
OKFDocument(
    frontmatter={"type": "A", "child": SomeObject()},
)
```

is invalid even when `SomeObject` itself supports `to_okf()`.

Whether a nested object should become an embedded mapping, a relation or another concept is a
bundle-level semantic decision and belongs to a future, separate contract.

### 9. New-document rendering and edit rendering stay separate

`dumps()` creates canonical new text. Its round-trip guarantee is semantic:

```text
producer
  -> OKFDocument
  -> dumps()
  -> parse_document_text()
  -> equivalent frontmatter and body
```

It is not a byte-preserving edit API.

Existing `apply`/edit paths operate on authored bytes and intentionally preserve concerns such as
quotes, comments, BOMs and line endings. They must not be routed through canonical new-document
serialization merely to share code.

The canonical frontmatter renderer should nevertheless be a reusable package primitive for other
**new-document producers**.

### 10. The protocol is Python-specific; the document is not

`SupportsOKF` and `to_okf()` are Python integration conveniences. TypeScript and Rust do not need a
method with the same spelling for parity. The emitted OKF must remain portable and conform to the
same parser semantics across runtimes.

## Public API

RFC 0020 adds four top-level Python exports:

```python
OKFDocument
SupportsOKF
to_okf
dumps
```

A dataclass can opt in without nominal coupling:

```python
from dataclasses import dataclass
from okf_parser import OKFDocument

@dataclass
class Processo:
    numero: str
    assunto: str

    def to_okf(self) -> OKFDocument:
        return OKFDocument(
            frontmatter={
                "type": "Processo",
                "numero": self.numero,
                "assunto": self.assunto,
            }
        )
```

Pydantic and slotted classes use the same method. Neither receives an implicit special case.

## Error model

| Situation | Result |
| --- | --- |
| native `OKFDocument` | returned directly |
| missing/non-callable `to_okf` | `TypeError` |
| hook returns another type | `TypeError` naming that type |
| producer hook raises | original exception propagates |
| invalid frontmatter Python value | `TypeError` |
| absent/non-string `type` | `TypeError` |
| blank `type` | `ValueError` |
| non-string body | `TypeError` |

The package does not wrap these ordinary conversion failures in a new exception hierarchy.

## Testing

The implementation must cover:

1. direct `OKFDocument` normalization;
2. structural dispatch without inheritance;
3. slotted, dataclass and Pydantic producers;
4. exactly one hook invocation;
5. missing/non-callable hooks;
6. invalid hook return types;
7. unchanged propagation of producer exceptions;
8. invalid frontmatter and body values;
9. absence and invalidity of `type`;
10. rejection of nested arbitrary objects;
11. top-level `type`/`title`/`description` canonical prefix;
12. deterministic ordering independent of insertion order;
13. recursive nested-map ordering with list order preserved;
14. YAML-sensitive strings such as `0012`, `false` and `null` round-tripping as strings;
15. Unicode and multiline body round-trip;
16. repeated byte-for-byte deterministic serialization.

## Alternatives considered

### `__okf__`

Rejected because package-defined dunders occupy a namespace Python reserves for system-defined names.

### ABC/base class

Rejected because nominal inheritance is unnecessary for a one-method capability.

### `@runtime_checkable Protocol`

Rejected for dispatch because it does not validate signatures and adds no value over calling and
validating the structural hook.

### `functools.singledispatch` registry

Deferred. It helps adapt third-party classes that cannot be modified, but adds global registration
state and precedence rules that the motivating use case does not need.

### `default=` callback like `json.dumps`

Deferred for the same reason and can be added compatibly later.

### Hook returns a dictionary

Rejected because a bare mapping cannot distinguish frontmatter from body and creates conventions the
stable `OKFDocument` value should own.

### Hook returns Markdown

Rejected because syntax, quoting, delimiters and canonicalization belong to the serializer.

## Non-goals

RFC 0020 does not define deserialization into arbitrary classes, bundle/object-graph serialization,
implicit dataclass or Pydantic conversion, recursive object conversion, filesystem writes, path or
identity derivation, an external adapter registry, or a cross-language `to_okf` method.

## Consequences

The final boundary is deliberately small:

```text
producer object        chooses semantic fields and body
OKFDocument            carries one validated concept value
okf-parser renderer    owns canonical physical OKF output
```

That gives Python applications an idiomatic opt-in serializer while keeping domain vocabulary out of
core, preserving cross-runtime OKF semantics and leaving style-preserving edit behavior untouched.
