"""Cross-runtime conformance tests for canonical schema exporter output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from okf_parser.schema_export import export_json_schema, export_zod_schema

if TYPE_CHECKING:
    from collections.abc import Iterator

_FIXTURE_PATH = Path(__file__).parents[1] / "conformance" / "schema-output.json"


def _cases() -> Iterator[dict[str, object]]:
    payload = cast("dict[str, object]", json.loads(_FIXTURE_PATH.read_text(encoding="utf-8")))
    yield from cast("list[dict[str, object]]", payload["cases"])


def test_shared_schema_output_corpus(tmp_path: Path) -> None:
    for case in _cases():
        root = tmp_path / cast("str", case["id"])
        root.mkdir()
        for document in cast("list[dict[str, str]]", case["documents"]):
            (root / document["name"]).write_text(document["content"], encoding="utf-8")

        options = cast("dict[str, object]", case["options"])
        infer_types = cast("bool", options["infer_types"])
        casts = cast("list[str]", options["casts"])
        report = export_json_schema(
            str(root),
            infer_types=infer_types,
            casts=casts,
        )

        assert report["schemas"] == case["schemas"], case["id"]
        assert report["total_types"] == len(cast("dict[str, object]", case["schemas"]))
        assert report["inferred_types"] is infer_types
        assert report["casts"] == casts
        assert export_zod_schema(str(root), infer_types=infer_types, casts=casts) == case["zod"], (
            case["id"]
        )


def test_astro_mode_changes_only_the_import(tmp_path: Path) -> None:
    root = tmp_path / "astro"
    root.mkdir()
    (root / "sample.md").write_text("---\ntype: sample\n---\nBody\n", encoding="utf-8")

    generic = export_zod_schema(str(root))
    astro = export_zod_schema(str(root), zod_import="astro")

    assert "import { z } from 'zod';" in generic
    assert astro == generic.replace("from 'zod'", "from 'astro:content'")
