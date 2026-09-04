"""Tests for the RFC 0020 Python object-to-OKF representation protocol."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel

from okf_parser import OKFDocument, OKFRepresentation, SupportsOKF, dumps, to_okf
from okf_parser.parser import parse_document_text


def test_native_document_is_already_normalized() -> None:
    document = OKFDocument(frontmatter={"type": "Reference"}, body="# Body\n")

    assert to_okf(document) is document


def test_mapping_requires_or_accepts_an_explicit_type() -> None:
    with pytest.raises(TypeError, match="type is required"):
        to_okf({"title": "Untyped"})

    document = to_okf({"title": "Typed"}, concept_type="Reference")

    assert document.frontmatter == {"type": "Reference", "title": "Typed"}


@dataclass
class Processo:
    """Test producer whose class name is also the inferred OKF type."""

    numero: str
    texto: str

    def __okf__(self) -> OKFRepresentation:
        """Project this process into OKF metadata plus body."""
        return OKFRepresentation(
            metadata={"numero": self.numero},
            body=self.texto,
        )


class CustomType:
    """Test producer that declares a type different from its class name."""

    def __okf__(self) -> dict[str, object]:
        """Project with an explicit OKF type override."""
        return {"type": "Pessoa", "nome": "Ana"}


class CountingProducer:
    """Test producer that records how often the protocol hook runs."""

    def __init__(self) -> None:
        """Initialize the hook invocation counter."""
        self.calls = 0

    def __okf__(self) -> dict[str, object]:
        """Count protocol invocation while returning metadata."""
        self.calls += 1
        return {"title": "Once"}


class PydanticExample(BaseModel):
    """Pydantic fixture for the default all-metadata projection."""

    title: str
    count: int
    active: bool
    ratio: float
    items: list[int]


def _consume_protocol(value: SupportsOKF) -> object:
    return value.__okf__()


def test_dunder_protocol_is_structural_and_infers_class_name() -> None:
    value = Processo("0001", "# Processo\n")

    representation = _consume_protocol(value)
    document = to_okf(value)

    assert isinstance(representation, OKFRepresentation)
    assert document.frontmatter == {"type": "Processo", "numero": "0001"}
    assert document.body == "# Processo\n"


def test_protocol_metadata_can_override_inferred_class_type() -> None:
    document = to_okf(CustomType())

    assert document.frontmatter == {"type": "Pessoa", "nome": "Ana"}


def test_caller_can_override_declared_or_inferred_type() -> None:
    document = to_okf(CustomType(), concept_type="Person")

    assert document.frontmatter["type"] == "Person"


def test_hook_is_called_exactly_once() -> None:
    value = CountingProducer()

    assert dumps(value).startswith("---\ntype: CountingProducer\n")
    assert value.calls == 1


def test_pydantic_models_default_to_metadata_and_infer_type() -> None:
    value = PydanticExample(
        title="Typed",
        count=12,
        active=False,
        ratio=1.5,
        items=[2, 1],
    )

    document = to_okf(value)

    assert document.frontmatter == {
        "type": "PydanticExample",
        "title": "Typed",
        "count": "12",
        "active": "false",
        "ratio": "1.5",
        "items": ["2", "1"],
    }
    assert document.body == ""


def test_pydantic_can_customize_yaml_body_split_with_dunder() -> None:
    class Article(BaseModel):
        title: str
        text: str

        def __okf__(self) -> OKFRepresentation:
            """Place article text in the OKF body rather than metadata."""
            return OKFRepresentation(metadata={"title": self.title}, body=self.text)

    document = to_okf(Article(title="News", text="# News\n\nBody."))

    assert document.frontmatter == {"type": "Article", "title": "News"}
    assert document.body == "# News\n\nBody."


def test_missing_or_non_callable_hook_is_rejected_for_plain_objects() -> None:
    class NonCallable:
        __okf__ = "not callable"

    with pytest.raises(TypeError, match="does not support OKF serialization"):
        to_okf(object())
    with pytest.raises(TypeError, match="does not support OKF serialization"):
        to_okf(NonCallable())


def test_invalid_hook_return_type_is_rejected() -> None:
    class Invalid:
        def __okf__(self) -> OKFRepresentation:
            """Return an invalid protocol result for validation coverage."""
            return cast("OKFRepresentation", 42)

    with pytest.raises(TypeError, match=r"__okf__.*must return"):
        to_okf(Invalid())


def test_hook_exception_propagates_unchanged() -> None:
    failure = RuntimeError("producer failed")

    class Broken:
        def __okf__(self) -> dict[str, object]:
            """Raise a producer error that the serializer must not wrap."""
            raise failure

    with pytest.raises(RuntimeError, match="producer failed") as caught:
        to_okf(Broken())

    assert caught.value is failure


def test_invalid_representation_envelope_is_rejected() -> None:
    with pytest.raises(TypeError, match="metadata must be a mapping"):
        OKFRepresentation(metadata=cast("dict[str, object]", []))
    with pytest.raises(TypeError, match="body must be a string"):
        OKFRepresentation(metadata={}, body=cast("str", 42))


def test_invalid_metadata_values_are_rejected() -> None:
    with pytest.raises(TypeError, match="string keys"):
        to_okf(cast("dict[str, object]", {1: "value"}), concept_type="Reference")
    with pytest.raises(TypeError, match="not JSON-like OKF metadata"):
        to_okf({"nested": object()}, concept_type="Reference")
    with pytest.raises(TypeError, match="non-finite"):
        to_okf({"ratio": float("nan")}, concept_type="Reference")


def test_invalid_declared_type_is_rejected() -> None:
    with pytest.raises(TypeError, match="type must be a string"):
        to_okf({"type": 42, "title": "Bad"})
    with pytest.raises(ValueError, match="type must be non-empty"):
        to_okf({"type": "   ", "title": "Bad"})


def test_canonical_order_is_independent_of_mapping_insertion_order() -> None:
    first = to_okf(
        {
            "zeta": "last",
            "description": "Description",
            "alpha": "first",
            "title": "Title",
            "nested": {"z": 2, "a": 1},
            "items": ["second", "first"],
        },
        concept_type="Reference",
    )
    second = to_okf(
        {
            "items": ["second", "first"],
            "nested": {"a": 1, "z": 2},
            "title": "Title",
            "alpha": "first",
            "description": "Description",
            "zeta": "last",
        },
        concept_type="Reference",
    )

    expected = (
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
    assert dumps(first) == expected


def test_yaml_sensitive_strings_unicode_and_body_round_trip() -> None:
    representation = OKFRepresentation(
        metadata={
            "type": "Reference",
            "active": False,
            "count": "0012",
            "empty": None,
            "literal_null": "null",
            "title": "Ação pública",
        },
        body="# Ação\n\nTexto sem newline final.",
    )

    document = to_okf(representation)
    text = dumps(document)
    parsed = parse_document_text(Path("concept.md"), text)

    assert parsed.frontmatter == dict(document.frontmatter)
    assert parsed.body == document.body
    assert dumps(document) == text
