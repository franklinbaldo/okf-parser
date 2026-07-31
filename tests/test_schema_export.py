"""Tests for JSON Schema and Zod export services using Pydantic."""

from pathlib import Path
from okf_parser.schema_export import export_json_schema, export_zod_schema, build_pydantic_models

def test_build_pydantic_models(tmp_path: Path) -> None:
    concept_dir = tmp_path / "concepts"
    concept_dir.mkdir()
    (concept_dir / "sample.md").write_text(
        "---\ntype: test_type\ntitle: Sample\nactive: true\ncount: 42\ntags: [a, b]\n---\nBody",
        encoding="utf-8",
    )

    models = build_pydantic_models(str(tmp_path))
    assert "test_type" in models
    model_cls = models["test_type"]
    assert model_cls.__name__ == "TestTypeConcept"


def test_export_json_schema_pydantic(tmp_path: Path) -> None:
    concept_dir = tmp_path / "concepts"
    concept_dir.mkdir()
    (concept_dir / "sample.md").write_text(
        "---\ntype: test_type\ntitle: Sample\nactive: true\ncount: 42\ntags: [a, b]\n---\nBody",
        encoding="utf-8",
    )

    report = export_json_schema(str(tmp_path))
    assert report["total_types"] == 1
    schemas = report["schemas"]
    assert "test_type" in schemas

    schema = schemas["test_type"]
    assert schema["title"] == "TestTypeConcept"
    assert "properties" in schema
    props = schema["properties"]
    assert props["type"]["const"] == "test_type"


def test_export_zod_schema_pydantic(tmp_path: Path) -> None:
    concept_dir = tmp_path / "concepts"
    concept_dir.mkdir()
    (concept_dir / "sample.md").write_text(
        "---\ntype: test_type\ntitle: Sample\n---\nBody",
        encoding="utf-8",
    )

    zod_code = export_zod_schema(str(tmp_path))
    assert "import { z } from 'astro:content';" in zod_code
    assert "export const TestTypeSchema = z.object({" in zod_code
    assert 'type: z.literal("test_type")' in zod_code
