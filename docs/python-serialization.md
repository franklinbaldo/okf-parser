---
type: Reference
title: Python object serialization
---

# Python object serialization

`okf-parser` can serialize an application object without requiring it to inherit from a parser base
class. The object opts in structurally by defining `to_okf()` and returning an `OKFDocument`.

```python
from dataclasses import dataclass

from okf_parser import OKFDocument, dumps

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
            },
            body=f"# Processo {self.numero}\n",
        )

text = dumps(Processo("0001234-56.2026.8.22.0001", "Cobrança"))
```

The public serialization surface is:

- `OKFDocument(frontmatter, body="")` — the validated semantic representation of one complete OKF
  concept document;
- `SupportsOKF` — a typing `Protocol` for classes that implement `to_okf()`; inheritance is not
  required;
- `to_okf(value)` — normalize an `OKFDocument` or call a compatible object's `to_okf()` exactly once;
- `dumps(value)` — render the normalized document as deterministic OKF Markdown.

`to_okf()` must return `OKFDocument`, not a dictionary and not Markdown text. The domain object owns
which semantic fields it exposes; the parser owns YAML quoting, canonical frontmatter ordering and
the `---` document envelope.

Frontmatter values use OKF's existing Python value contract: strings, `None`, lists of supported
values, and string-keyed mappings of supported values. Python integers, floats, booleans and arbitrary
nested objects are not implicitly converted. This preserves the parser's scalar-spelling semantics
and prevents private Python representation details from leaking into OKF.

The renderer uses the repository's canonical physical order for new documents: `type`, `title`, and
`description` first when present, followed by the remaining keys in lexical order. Nested mappings
are sorted recursively; list order is preserved. This ordering is physical only and does not change
OKF semantics.

The API intentionally does not use a package-defined `__okf__` dunder. Python reserves double-leading
and double-trailing names for system-defined behavior. A normal structural method gives the same duck
typing ergonomics without occupying that namespace.

Serialization creates a new canonical document; it is not a byte-preserving edit operation. Existing
document writes continue through the parser's style-preserving edit/apply surfaces when comments,
quotes, BOMs or original line endings matter.
