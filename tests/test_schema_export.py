"""Tests for string-first JSON Schema and Zod export."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.schema_export import SchemaCastError, export_json_schema, export_zod_schema

if TYPE_CHECKING:
    from pathlib import Path


def _write_concept(path: Path, frontmatter: str) -> None:
    path.write_text(f"---\n{frontmatter}---\nBody\n", encoding="utf-8")


def test_schema_keeps_scalars_as_strings_by_default(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "sample.md",
        "type: test_type\nactive: true\ncount: 42\ncreated: 2026-01-01\n",
    )

    schema = export_json_schema(str(tmp_path))["schemas"]["test_type"]
    properties = schema["properties"]

    assert properties["active"]["type"] == "string"
    assert properties["count"]["type"] == "string"
    assert properties["created"]["type"] == "string"
    assert properties["type"]["const"] == "test_type"


def test_schema_infers_types_from_every_observation_with_pandas(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "one.md",
        "type: test_type\nactive: true\ncount: 42\ncreated: 2026-01-01\n",
    )
    _write_concept(
        tmp_path / "two.md",
        "type: test_type\nactive: false\ncount: 7\ncreated: 2026-02-01\n",
    )

    report = export_json_schema(str(tmp_path), infer_types=True)
    properties = report["schemas"]["test_type"]["properties"]

    assert report["inferred_types"] is True
    assert properties["active"]["type"] == "boolean"
    assert properties["count"]["type"] == "integer"
    assert properties["created"] == {
        "format": "date",
        "title": "Created",
        "type": "string",
    }


def test_inference_falls_back_to_string_when_one_value_is_incompatible(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: test_type\ncount: 42\n")
    _write_concept(tmp_path / "two.md", "type: test_type\ncount: unknown\n")

    schema = export_json_schema(str(tmp_path), infer_types=True)["schemas"]["test_type"]

    assert schema["properties"]["count"]["type"] == "string"


def test_explicit_cast_overrides_default_and_is_strict(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: test_type\ncount: 42\n")
    _write_concept(tmp_path / "two.md", "type: test_type\ncount: 7\n")

    report = export_json_schema(str(tmp_path), casts=["count=integer"])

    assert report["casts"] == ["count=integer"]
    assert report["schemas"]["test_type"]["properties"]["count"]["type"] == "integer"

    with pytest.raises(SchemaCastError, match="cannot cast 'count' to date"):
        export_json_schema(str(tmp_path), casts=["count=date"])


def test_unknown_or_invalid_cast_is_reported(tmp_path: Path) -> None:
    _write_concept(tmp_path / "sample.md", "type: test_type\ncount: 42\n")

    with pytest.raises(SchemaCastError, match="cast field was not found"):
        export_json_schema(str(tmp_path), casts=["missing=integer"])
    with pytest.raises(SchemaCastError, match="expected FIELD=TYPE"):
        export_json_schema(str(tmp_path), casts=["count=uuid"])


def test_list_items_are_inferred_together(tmp_path: Path) -> None:
    _write_concept(tmp_path / "one.md", "type: test_type\nvalues: [1, 2]\n")
    _write_concept(tmp_path / "two.md", "type: test_type\nvalues: [3]\n")

    schema = export_json_schema(str(tmp_path), infer_types=True)["schemas"]["test_type"]

    assert schema["properties"]["values"]["items"]["type"] == "integer"


def test_zod_uses_the_same_inferred_schema(tmp_path: Path) -> None:
    _write_concept(
        tmp_path / "sample.md",
        "type: test_type\nactive: true\ncount: 42\ncreated: 2026-01-01\n",
    )

    zod = export_zod_schema(str(tmp_path), infer_types=True)

    assert "export const TestTypeSchema = z.object({" in zod
    assert '"active": z.boolean()' in zod
    assert '"count": z.number().int()' in zod
    assert '"created": z.string().date()' in zod
    assert '"type": z.literal("test_type")' in zod
