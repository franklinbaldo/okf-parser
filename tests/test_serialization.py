"""Tests for the RFC 0020 Python object-to-OKF serialization protocol."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel

from okf_parser import OKFDocument, SupportsOKF, dumps, to_okf
from okf_parser.parser import parse_document_text

if TYPE_CHECKING:
    from okf_parser.models import YamlValue


def test_native_document_is_already_normalized() -> None:
    document = OKFDocument(frontmatter={"type": "Reference"}, body="# Body\n")

    assert to_okf(document) is document


@dataclass
class _DataclassConcept:
    title: str

    def to_okf(self) -> OKFDocument:
        return OKFDocument(
            frontmatter={"type": "Reference", "title": self.title},
            body=f"# {self.title}\n",
        )


class _SlottedConcept:
    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls = 0

    def to_okf(self) -> OKFDocument:
        self.calls += 1
        return OKFDocument(frontmatter={"type": "Reference"})


class _PydanticConcept(BaseModel):
    title: str

    def to_okf(self) -> OKFDocument:
        return OKFDocument(frontmatter={"type": "Reference", "title": self.title})


def _consume_protocol(value: SupportsOKF) -> OKFDocument:
    return value.to_okf()


def test_structural_protocol_needs_no_inheritance() -> None:
    value = _DataclassConcept("Example")

    assert _consume_protocol(value).frontmatter["title"] == "Example"
    assert to_okf(value).body == "# Example\n"


def test_slotted_hook_is_called_exactly_once() -> None:
    value = _SlottedConcept()

    assert dumps(value).startswith("---\ntype: Reference\n---\n")
    assert value.calls == 1


def test_pydantic_model_opts_in_explicitly() -> None:
    value = _PydanticConcept(title="Typed")

    assert to_okf(value).frontmatter["title"] == "Typed"


def test_missing_or_non_callable_hook_is_rejected() -> None:
    class NonCallable:
        to_okf = "not callable"

    with pytest.raises(TypeError, match="does not support OKF serialization"):
        to_okf(cast("SupportsOKF", object()))
    with pytest.raises(TypeError, match="does not support OKF serialization"):
        to_okf(cast("SupportsOKF", NonCallable()))


def test_invalid_hook_return_type_is_rejected() -> None:
    class Invalid:
        def to_okf(self) -> OKFDocument:
            return cast("OKFDocument", {"type": "Reference"})

    with pytest.raises(TypeError, match="must return OKFDocument, not dict"):
        to_okf(Invalid())


def test_hook_exception_propagates_unchanged() -> None:
    failure = RuntimeError("producer failed")

    class Broken:
        def to_okf(self) -> OKFDocument:
            raise failure

    with pytest.raises(RuntimeError, match="producer failed") as caught:
        to_okf(Broken())

    assert caught.value is failure


@pytest.mark.parametrize(
    ("frontmatter", "error", "message"),
    [
        ([], TypeError, "frontmatter must be a mapping"),
        ({}, TypeError, "must contain a string type"),
        ({"type": None}, TypeError, "must contain a string type"),
        ({"type": "   "}, ValueError, "type must be non-empty"),
        ({"type": 42}, TypeError, "only string keys and OKF YAML values"),
        ({"type": "Reference", "count": 42}, TypeError, "only string keys and OKF YAML values"),
    ],
)
def test_invalid_document_values_fail_before_rendering(
    frontmatter: object,
    error: type[Exception],
    message: str,
) -> None:
    typed_frontmatter = cast("dict[str, YamlValue]", frontmatter)

    with pytest.raises(error, match=message):
        OKFDocument(frontmatter=typed_frontmatter)


def test_non_string_body_is_rejected() -> None:
    with pytest.raises(TypeError, match="body must be a string"):
        OKFDocument(frontmatter={"type": "Reference"}, body=cast("str", 42))


def test_nested_objects_are_not_recursively_serialized() -> None:
    nested = _DataclassConcept("Nested")
    frontmatter = {
        "type": "Reference",
        "nested": cast("YamlValue", nested),
    }

    with pytest.raises(TypeError, match="only string keys and OKF YAML values"):
        OKFDocument(frontmatter=frontmatter)


def test_canonical_order_is_independent_of_mapping_insertion_order() -> None:
    first = OKFDocument(
        frontmatter={
            "zeta": "last",
            "description": "Description",
            "type": "Reference",
            "alpha": "first",
            "title": "Title",
            "nested": {"z": "2", "a": "1"},
            "items": ["second", "first"],
        }
    )
    second = OKFDocument(
        frontmatter={
            "items": ["second", "first"],
            "nested": {"a": "1", "z": "2"},
            "title": "Title",
            "alpha": "first",
            "type": "Reference",
            "description": "Description",
            "zeta": "last",
        }
    )

    expected_prefix = (
        "---\n"
        "type: Reference\n"
        "title: Title\n"
        "description: Description\n"
        "alpha: first\n"
        "items:\n"
        "- second\n"
        "- first\n"
        "nested:\n"
        "  a: '1'\n"
        "  z: '2'\n"
        "zeta: last\n"
        "---\n"
    )

    assert dumps(first) == dumps(second)
    assert dumps(first) == expected_prefix


def test_yaml_sensitive_strings_unicode_and_body_round_trip() -> None:
    document = OKFDocument(
        frontmatter={
            "type": "Reference",
            "active": "false",
            "count": "0012",
            "empty": None,
            "literal_null": "null",
            "title": "Ação pública",
        },
        body="# Ação\n\nTexto sem newline final.",
    )

    text = dumps(document)
    parsed = parse_document_text(Path("concept.md"), text)

    assert parsed.frontmatter == dict(document.frontmatter)
    assert parsed.body == document.body
    assert dumps(document) == text
