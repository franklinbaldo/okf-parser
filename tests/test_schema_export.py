"""Tests for JSON Schema and Zod export services."""

from pathlib import Path
import json
from okf_parser.schema_export import export_json_schema, export_zod_schema

def test_export_json_schema(tmp_path: Path) -> None:
    # Create sample concept
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
    props = schema["properties"]
    assert props["type"]["const"] == "test_type"
    assert props["active"]["type"] == "boolean"
    assert props["count"]["type"] == "integer"
    assert props["tags"]["type"] == "array"

def test_export_zod_schema(tmp_path: Path) -> None:
    concept_dir = tmp_path / "concepts"
    concept_dir.mkdir()
    (concept_dir / "sample.md").write_text(
        "---\ntype: test_type\ntitle: Sample\n---\nBody",
        encoding="utf-8",
    )

    zod_code = export_zod_schema(str(tmp_path))
    assert "import { z } from 'astro:content';" in zod_code
    assert "export const TestTypeSchema = z.object({" in zod_code
    assert "type: z.literal(\"test_type\")" in zod_code
