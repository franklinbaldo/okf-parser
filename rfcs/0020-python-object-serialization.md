---
type: RFC
title: Python object-to-OKF serialization protocol
status: proposed
description: Define a structural Python protocol and canonical document value for converting application objects into deterministic OKF without inventing a custom dunder or leaking YAML syntax into domain models
---

# RFC 0020: Python object-to-OKF serialization protocol

## Summary

`okf-parser` should expose a small Python-native boundary for turning application objects into one
complete OKF document. The boundary is structural: an object may implement an ordinary `to_okf()`
method returning a parser-owned `OKFDocument`. The package then validates and renders that value.

```python
from okf_parser import OKFDocument, dumps

class Pessoa:
    def __init__(self, nome: str, biografia: str) -> None:
        self.nome = nome
        self.biografia = biografia

    def to_okf(self) -> OKFDocument:
        return OKFDocument(
            frontmatter={"type": "Pessoa", "nome": self.nome},
            body=self.biografia,
        )

text = dumps(Pessoa("Ada", "Primeira programadora."))
```

The object owns its semantic projection. `okf-parser` owns the OKF value contract, validation,
canonical YAML rendering and Markdown envelope.

This RFC resolves #233. It is deliberately separate from #67: #67 projects already-parsed parser
state outward as JSON-ready application data; this RFC projects an application object inward to an
OKF document.

## Motivation

The package already owns parsing, validation, canonical concepts and guarded filesystem writes, but
an application that wants to produce OKF from a Python object still has to know too much about the
physical format. Typical consumers otherwise end up doing some combination of:

- building YAML themselves;
- choosing quoting and key ordering independently;
- reproducing the `---` envelope;
- reflecting over `__dict__`, dataclasses or Pydantic models;
- coupling domain classes to parser-internal write helpers.

Those choices create multiple informal serializers around one canonical parser. A first-class value
and conversion protocol make the boundary explicit without teaching `okf-parser` any producer
vocabulary.

## Decision

### 1. Use an ordinary structural method, not `__okf__`

The motivating idea used `__okf__()`. This RFC rejects that spelling for the public protocol.

Python reserves identifiers of the form `__*__` for system-defined names and explicitly warns that
undocumented uses may break as the interpreter evolves. A library-specific dunder is therefore not
the most conservative or canonical extension point when an ordinary method works just as well.

The protocol method is:

```python
def to_okf(self) -> OKFDocument: ...
```

No inheritance or registration is required. Any object with the correct method structurally
satisfies the contract.

### 2. Publish `SupportsOKF` for static structural typing

The package exposes:

```python
from typing import Protocol

class SupportsOKF(Protocol):
    def to_okf(self) -> OKFDocument: ...
```

`Protocol` is the standard typing mechanism for structural subtyping. A domain class should not
inherit from `SupportsOKF`; implementing the method is sufficient for type checkers.

`SupportsOKF` is not decorated with `@runtime_checkable`. Runtime-checkable protocols test method
presence rather than signatures, and Python documents their `isinstance()` checks as potentially
more expensive than ordinary attribute checks. Runtime dispatch here needs neither nominal identity
nor protocol introspection.

### 3. `OKFDocument` is the only accepted semantic return value

The parser owns a compact document value:

```python
from collections.abc import Mapping
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class OKFDocument:
    frontmatter: Mapping[str, YamlValue]
    body: str = ""
```

The dataclass is frozen and slotted so its public shape is explicit and cheap. Construction or
normalization copies and validates the mapping against the parser's existing `YamlValue` contract;
the value does not widen OKF scalar semantics just because Python has additional scalar types.

The representation is semantic rather than physical. It contains no BOM, newline policy, delimiter
spelling, YAML emitter object, destination path or filesystem state.

### 4. Normalize through `to_okf(value)`

The public normalizer is:

```python
def to_okf(value: OKFDocument | SupportsOKF) -> OKFDocument: ...
```

Runtime rules are intentionally small:

1. if `value` is already `OKFDocument`, return it directly;
2. otherwise obtain `value.to_okf` structurally;
3. if it is absent or not callable, raise `TypeError`;
4. call it exactly once;
5. require the result to be `OKFDocument`, otherwise raise `TypeError`;
6. do not catch exceptions raised by the application's method.

Calling the hook once matters: domain conversion may be computed, stateful or expensive, and a
serializer must not make duplicated conversion observable.

The parser does not fall back to `__dict__`, `dataclasses.asdict()`, Pydantic `model_dump()` or
attribute reflection. Such reflection is convenient but changes private implementation details into
wire format by accident. Explicit projection keeps domain ownership with the producer.

### 5. Render through `dumps(value)`

The textual API is:

```python
def dumps(value: OKFDocument | SupportsOKF) -> str: ...
```

`dumps()` first calls the normalization primitive, then renders one complete UTF-8-compatible OKF
Markdown string. It does not write a path and has no filesystem effects.

A separate `dump(value, fp)` is deferred. It is a trivial convenience once a real consumer needs it,
but adding it now would expand API and typing surface without adding serialization semantics.

### 6. Validate before emission

`OKFDocument`/normalization must reject values that cannot represent a conformant concept document:

- frontmatter must be a string-keyed mapping whose values satisfy the existing recursive
  `YamlValue` contract;
- `type` must exist, be a string and remain non-empty after stripping;
- `body` must be a string.

Invalid Python objects fail before text is emitted. The public exception for an invalid document
value is `ValueError` when the shape is representable but violates the OKF document contract, and
`TypeError` when a Python value has the wrong Python type or cannot participate in the protocol.

The serializer should reuse the same parser-side validation vocabulary rather than create a second
wider frontmatter type.

### 7. Canonicalize physical ordering deterministically

For a new document there is no authored key ordering to preserve. The renderer therefore has a
canonical order:

1. `type` first;
2. remaining top-level keys ordered lexicographically by Python string code point;
3. nested mappings recursively ordered by the same rule;
4. list order preserved exactly.

Canonical ordering makes semantically equal producer mappings render identically even when they were
constructed in different insertion orders. It also avoids locale-dependent ordering.

The renderer must configure one package-owned YAML emitter and expose the frontmatter-rendering
primitive internally so other **new-document** producers can reuse it. Existing-document writers
that preserve authored YAML style remain a different concern and must not be forced through the
canonical new-document renderer.

### 8. Do not recursively serialize arbitrary objects inside frontmatter

`to_okf()` means “represent this object as one complete OKF document.” It does not mean “walk every
object graph and call similarly named methods.”

Nested frontmatter values remain `YamlValue` only. In particular:

```python
OKFDocument(
    frontmatter={"type": "A", "child": SomeOtherObject()},
)
```

is invalid even if `SomeOtherObject` implements `to_okf()`.

This avoids ambiguous questions about whether nested objects become mappings, links, embedded
concepts or separate documents. A future bundle-level producer protocol may answer those questions
separately.

### 9. Round-trip is semantic, not byte-preserving

`dumps()` creates a new canonical document. Its contract is:

```text
object
  -> OKFDocument
  -> canonical OKF text
  -> parse_document_text(...)
  -> equivalent frontmatter/body semantics
```

It does not promise to reproduce an existing document byte-for-byte. Byte/style preservation belongs
to the existing edit/apply write path, where comments, quoting, BOM and line endings originate from
real authored bytes.

### 10. Python-only protocol; OKF semantics remain cross-runtime

`to_okf()` and `SupportsOKF` are Python integration conveniences, so TypeScript and Rust do not need
syntactic equivalents for parity. What must remain portable is the emitted OKF document itself. A
serialized document must pass the same conformance rules consumed by the other runtimes.

## Public API

The first implementation exports these names from `okf_parser`:

```python
OKFDocument
SupportsOKF
to_okf
dumps
```

A typical dataclass remains independent of the parser's inheritance tree:

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

Pydantic models work the same way: they opt in with a method instead of receiving an implicit
`model_dump()` special case.

## Error model

Errors should be local and unsurprising:

| Situation | Result |
| --- | --- |
| `OKFDocument` input | returned directly |
| object has no `to_okf` | `TypeError` |
| `to_okf` exists but is not callable | `TypeError` |
| hook returns dict/string/other type | `TypeError` naming returned type |
| hook itself raises | original exception propagates |
| invalid frontmatter Python shape | `TypeError` |
| absent/blank/non-string `type` | `ValueError` or `TypeError` according to Python type |
| non-string body | `TypeError` |

The API should not wrap every failure in a package-specific exception. These are ordinary Python
conversion errors and preserving their native classes makes them compose naturally with callers.

## Internal rendering boundary

The implementation should introduce one small package-internal helper for canonical frontmatter
rendering rather than copy the `StringIO` + `ruamel.yaml` setup already present in producers such as
`bundle_import.py`.

The helper is for newly generated YAML and may be reused by `bundle_import` when doing so preserves
that command's established semantics. Existing apply/edit code uses round-trip YAML specifically to
preserve authored style and is not required to migrate.

This distinction prevents an attractive but incorrect refactor where canonical serialization would
silently reorder an existing user's frontmatter during an edit.

## Testing

The implementation must cover at least:

1. direct `OKFDocument` normalization;
2. plain object structural dispatch without inheritance;
3. slotted object dispatch;
4. dataclass opt-in;
5. Pydantic opt-in;
6. hook called exactly once;
7. missing/non-callable method;
8. invalid hook return type;
9. propagation of an application exception unchanged;
10. missing, blank and wrong-type `type`;
11. invalid nested Python values rejected instead of reflected/converted;
12. canonical key ordering independent of insertion order;
13. nested mapping ordering with list order preserved;
14. YAML-sensitive strings round-trip as strings;
15. Unicode round-trip;
16. body round-trip including empty and multiline bodies;
17. `parse_document_text()` accepts emitted text and sees equivalent semantics;
18. repeated serialization is byte-for-byte deterministic.

## Alternatives considered

### Custom `__okf__` dunder

Rejected. It is attractive because it resembles interpreter protocols such as `__fspath__`, but
those names are useful precisely because Python itself standardizes them. A package-defined dunder
occupies a namespace Python reserves for future system behavior.

### ABC/base class

Rejected. Requiring domain classes to inherit from an `OKFSerializable` base is nominal coupling for
a one-method protocol. Structural typing is the more idiomatic fit.

### `@runtime_checkable Protocol`

Rejected for dispatch. It adds no validation of the return signature and is unnecessary when the
implementation must call and validate the method anyway. The static Protocol remains valuable.

### `functools.singledispatch` registry

Deferred. Registries are useful when serializing third-party classes that cannot be modified, but
they introduce global registration state, precedence rules and plugin lifecycle. The motivating use
case controls its domain classes and needs only explicit opt-in.

### Generic `default=` callback like `json.dumps`

Deferred for the same reason. It can be added compatibly if an external-class adapter becomes a real
consumer requirement.

### Return a dict from the hook

Rejected. A bare dict cannot distinguish frontmatter from body, makes future document-level metadata
awkward, and encourages renderer-specific conventions. `OKFDocument` gives the semantic boundary a
stable name and type.

### Return Markdown from the hook

Rejected. That pushes YAML quoting, delimiter handling and canonicalization into every producer and
prevents parser-owned validation before emission.

## Non-goals

This RFC does not define deserialization back into arbitrary application classes, a bundle-level
object graph serializer, implicit dataclass/Pydantic serialization, recursive object conversion,
filesystem writes, identity/path derivation, external adapter registries, or a cross-language method
named `to_okf`.

## Consequences

Application models gain a tiny explicit opt-in surface while remaining otherwise independent of the
parser. The serializer becomes deterministic and testable, generated OKF remains portable, and the
package gains a reusable new-document rendering primitive without disturbing style-preserving edit
semantics.

Most importantly, the API distinguishes three responsibilities cleanly:

```text
producer object        chooses semantic fields/body
OKFDocument            represents one valid OKF concept value
okf-parser renderer    owns syntax and canonical physical output
```

That boundary is small enough to stay stable even if YAML/rendering internals evolve later.
