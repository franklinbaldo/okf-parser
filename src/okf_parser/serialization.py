"""Serialize Python values into canonical OKF documents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import StringIO
from typing import Protocol

from pydantic import ValidationError
from ruamel.yaml import YAML

from okf_parser.frontmatter_order import frontmatter_key_order
from okf_parser.models import FRONTMATTER_ADAPTER, YamlValue


@dataclass(frozen=True, slots=True)
class OKFDocument:
    """One validated semantic OKF concept document."""

    frontmatter: Mapping[str, YamlValue]
    body: str = ""

    def __post_init__(self) -> None:
        """Validate and detach the semantic document value from producer mappings."""
        if not isinstance(self.frontmatter, Mapping):
            msg = "OKFDocument.frontmatter must be a mapping"
            raise TypeError(msg)
        try:
            frontmatter = FRONTMATTER_ADAPTER.validate_python(dict(self.frontmatter), strict=True)
        except (TypeError, ValueError, ValidationError) as exc:
            msg = "OKFDocument.frontmatter must contain only string keys and OKF YAML values"
            raise TypeError(msg) from exc

        concept_type = frontmatter.get("type")
        if not isinstance(concept_type, str):
            msg = "OKFDocument.frontmatter must contain a string type"
            raise TypeError(msg)
        if not concept_type.strip():
            msg = "OKFDocument.frontmatter type must be non-empty"
            raise ValueError(msg)
        if not isinstance(self.body, str):
            msg = "OKFDocument.body must be a string"
            raise TypeError(msg)

        object.__setattr__(self, "frontmatter", frontmatter)


class SupportsOKF(Protocol):
    """Structural typing contract for values that project themselves to OKF."""

    def to_okf(self) -> OKFDocument:
        """Return this value's semantic OKF document representation."""
        ...


def to_okf(value: OKFDocument | SupportsOKF) -> OKFDocument:
    """Normalize a native document or a structurally compatible Python value."""
    if isinstance(value, OKFDocument):
        return value

    hook = getattr(value, "to_okf", None)
    if not callable(hook):
        msg = f"object of type {type(value).__qualname__!r} does not support OKF serialization"
        raise TypeError(msg)

    document = hook()
    if not isinstance(document, OKFDocument):
        msg = (
            f"{type(value).__qualname__}.to_okf() must return OKFDocument, "
            f"not {type(document).__qualname__}"
        )
        raise TypeError(msg)
    return document


def _canonicalize_value(value: YamlValue) -> YamlValue:
    """Order mappings deterministically while preserving list order."""
    if isinstance(value, dict):
        return {key: _canonicalize_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize_value(item) for item in value]
    return value


def render_frontmatter(frontmatter: Mapping[str, YamlValue]) -> str:
    """Render new-document frontmatter in canonical physical order."""
    ordered = {
        key: _canonicalize_value(frontmatter[key])
        for key in sorted(frontmatter, key=frontmatter_key_order)
    }
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    yaml.width = 4096
    buffer = StringIO()
    yaml.dump(ordered, buffer)
    return buffer.getvalue()


def dumps(value: OKFDocument | SupportsOKF) -> str:
    """Serialize one Python value as deterministic OKF Markdown text."""
    document = to_okf(value)
    frontmatter = render_frontmatter(document.frontmatter)
    if not frontmatter.endswith("\n"):
        frontmatter += "\n"
    return f"---\n{frontmatter}---\n{document.body}"
