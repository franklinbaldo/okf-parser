"""The canonical JSON spelling of OKF frontmatter (RFC 8785/JCS).

The digests themselves are computed by the native engine; this is the one
spelling the shell still produces, for ``ParsedDocument.frontmatter_json``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from okf_parser.models import YamlValue

_SURROGATE_MIN = 0xD800
_SURROGATE_MAX = 0xDFFF


def _reject_lone_surrogates(value: str) -> None:
    if any(_SURROGATE_MIN <= ord(char) <= _SURROGATE_MAX for char in value):
        msg = "JCS strings must not contain lone UTF-16 surrogate code points"
        raise ValueError(msg)


def _jcs_string(value: str) -> str:
    _reject_lone_surrogates(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utf16_sort_key(value: str) -> bytes:
    _reject_lone_surrogates(value)
    return value.encode("utf-16-be")


def canonical_json(value: YamlValue) -> str:
    """Serialize the OKF frontmatter value subset using RFC 8785/JCS rules.

    OKF failsafe frontmatter preserves ordinary scalars as strings, so this
    digest domain only needs null, strings, arrays and objects. Object keys are
    ordered by unsigned UTF-16 code units as required by JCS; no locale or
    host-language object enumeration order participates in the result.
    """
    if value is None:
        return "null"
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    items = (
        f"{_jcs_string(key)}:{canonical_json(value[key])}"
        for key in sorted(value, key=_utf16_sort_key)
    )
    return "{" + ",".join(items) + "}"
