"""Serialize Python and JSON-like values into canonical OKF documents."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import StringIO
from typing import Protocol

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML

from okf_parser.frontmatter_order import frontmatter_key_order
from okf_parser.models import FRONTMATTER_ADAPTER, YamlValue

_MISSING_HOOK = object()


@dataclass(frozen=True, slots=True)
class OKFRepresentation:
    """A producer's semantic OKF projection before the required type is resolved."""

    metadata: Mapping[str, object]
    body: str = ""

    def __post_init__(self) -> None:
        """Validate and detach the producer-facing representation envelope."""
        if not isinstance(self.metadata, Mapping):
            msg = "OKFRepresentation.metadata must be a mapping"
            raise TypeError(msg)
        if not isinstance(self.body, str):
            msg = "OKFRepresentation.body must be a string"
            raise TypeError(msg)

        metadata: dict[str, object] = {}
        for key, item in self.metadata.items():
            if not isinstance(key, str):
                msg = "OKF metadata mappings must use string keys"
                raise TypeError(msg)
            metadata[key] = item
        object.__setattr__(self, "metadata", metadata)


@dataclass(frozen=True, slots=True)
class OKFDocument:
    """One validated semantic OKF concept document with a resolved type."""

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
    """Structural typing contract for values that define their own OKF projection."""

    def __okf__(self) -> OKFRepresentation | OKFDocument | Mapping[str, object]:
        """Return this value's semantic OKF representation."""
        ...


def _json_scalar_to_okf(value: object) -> str | None:
    """Convert one JSON scalar to the parser's spelling-preserving scalar domain."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = "OKF metadata does not support non-finite JSON numbers"
            raise TypeError(msg)
        return json.dumps(value, allow_nan=False)
    msg = f"unsupported OKF metadata scalar {type(value).__qualname__!r}"
    raise TypeError(msg)


def _metadata_value_to_okf(value: object) -> YamlValue:
    """Normalize JSON-like producer data into OKF's spelling-preserving value domain."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return _json_scalar_to_okf(value)
    if isinstance(value, Mapping):
        normalized: dict[str, YamlValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = "OKF metadata mappings must use string keys"
                raise TypeError(msg)
            normalized[key] = _metadata_value_to_okf(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_metadata_value_to_okf(item) for item in value]
    msg = f"object of type {type(value).__qualname__!r} is not JSON-like OKF metadata"
    raise TypeError(msg)


def _validated_type(value: object, *, label: str) -> str:
    """Return one valid non-empty OKF type spelling or raise a precise input error."""
    if not isinstance(value, str):
        msg = f"{label} must be a string"
        raise TypeError(msg)
    if not value.strip():
        msg = f"{label} must be non-empty"
        raise ValueError(msg)
    return value


def _representation_from_hook(
    value: object,
    hook: Callable[[], object],
) -> OKFRepresentation | OKFDocument:
    """Invoke a resolved producer hook exactly once and normalize its result envelope."""
    representation = hook()
    if isinstance(representation, OKFDocument | OKFRepresentation):
        return representation
    if isinstance(representation, Mapping):
        metadata: dict[str, object] = {}
        for key, item in representation.items():
            if not isinstance(key, str):
                msg = "OKF metadata mappings must use string keys"
                raise TypeError(msg)
            metadata[key] = item
        return OKFRepresentation(metadata=metadata)
    msg = (
        f"{type(value).__qualname__}.__okf__() must return OKFRepresentation, "
        f"OKFDocument, or a mapping, not {type(representation).__qualname__}"
    )
    raise TypeError(msg)


def _resolve_representation(value: object) -> tuple[OKFRepresentation | OKFDocument, str | None]:
    """Resolve supported Python inputs and preserve the source class for type inference."""
    if isinstance(value, OKFDocument | OKFRepresentation):
        return value, None

    hook = getattr(value, "__okf__", _MISSING_HOOK)
    if hook is not _MISSING_HOOK:
        if not callable(hook):
            msg = f"object of type {type(value).__qualname__!r} defines non-callable __okf__"
            raise TypeError(msg)
        return _representation_from_hook(value, hook), type(value).__name__

    if isinstance(value, BaseModel):
        metadata = value.model_dump(mode="json")
        if not isinstance(metadata, Mapping):
            msg = (
                "Pydantic default OKF projection must be a mapping; "
                "define __okf__ for root/scalar models"
            )
            raise TypeError(msg)
        return OKFRepresentation(metadata=metadata), type(value).__name__

    if isinstance(value, Mapping):
        metadata: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = "OKF metadata mappings must use string keys"
                raise TypeError(msg)
            metadata[key] = item
        return OKFRepresentation(metadata=metadata), None

    msg = f"object of type {type(value).__qualname__!r} does not support OKF serialization"
    raise TypeError(msg)


def to_okf(value: object, *, concept_type: str | None = None) -> OKFDocument:
    """Convert a supported Python value into one validated semantic OKF document."""
    explicit_type = None
    if concept_type is not None:
        explicit_type = _validated_type(concept_type, label="concept_type")

    representation, inferred_type = _resolve_representation(value)
    if isinstance(representation, OKFDocument):
        if explicit_type is None:
            return representation
        frontmatter = dict(representation.frontmatter)
        frontmatter["type"] = explicit_type
        return OKFDocument(frontmatter=frontmatter, body=representation.body)

    metadata = dict(representation.metadata)
    has_declared_type = "type" in metadata
    declared_type: str | None = None
    if has_declared_type:
        declared_type = _validated_type(metadata.pop("type"), label="OKF metadata type")

    if explicit_type is not None:
        resolved_type: object = explicit_type
    elif declared_type is not None:
        resolved_type = declared_type
    else:
        resolved_type = inferred_type

    if resolved_type is None:
        msg = "OKF type is required for mapping/representation inputs"
        raise TypeError(msg)
    resolved_type = _validated_type(resolved_type, label="OKF type")

    normalized = _metadata_value_to_okf(metadata)
    if not isinstance(normalized, dict):
        msg = "OKF metadata must normalize to a mapping"
        raise TypeError(msg)

    frontmatter: dict[str, YamlValue] = {"type": resolved_type, **normalized}
    return OKFDocument(frontmatter=frontmatter, body=representation.body)


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


def dumps(value: object, *, concept_type: str | None = None) -> str:
    """Serialize a supported Python value as deterministic OKF Markdown text."""
    document = to_okf(value, concept_type=concept_type)
    frontmatter = render_frontmatter(document.frontmatter)
    if not frontmatter.endswith("\n"):
        frontmatter += "\n"
    return f"---\n{frontmatter}---\n{document.body}"
