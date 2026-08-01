"""Language-neutral compatibility cases shared with the TypeScript package."""

from __future__ import annotations

import json
from pathlib import Path

from okf_parser.parser import parse_document


def test_shared_frontmatter_conformance(tmp_path: Path) -> None:
    cases = json.loads(
        (Path(__file__).parents[1] / "conformance" / "frontmatter.json").read_text(encoding="utf-8")
    )
    for index, case in enumerate(cases):
        document = tmp_path / f"case-{index}.md"
        document.write_text(case["source"], encoding="utf-8")

        parsed = parse_document(document)

        assert parsed.frontmatter == case["frontmatter"], case["name"]
        assert parsed.body == case["body"], case["name"]
