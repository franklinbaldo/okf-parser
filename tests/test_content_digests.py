"""Cross-language deterministic source and parsed-content identity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from okf_parser import load_bundle
from okf_parser.digests import canonical_json
from okf_parser.parser import parse_document_text


def _vectors() -> list[dict[str, str]]:
    path = Path(__file__).parents[1] / "conformance" / "content-digests.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return cast("list[dict[str, str]]", payload["cases"])


def test_content_digest_vectors_are_pinned() -> None:
    for case in _vectors():
        parsed = parse_document_text(Path(f"{case['name']}.md"), case["source"])
        assert parsed.source_digest == case["source_digest"]
        assert parsed.parsed_digest == case["parsed_digest"]
        assert canonical_json([parsed.frontmatter, parsed.body]) == case["parsed_canonical"]


def test_physical_variant_changes_source_not_parsed_value() -> None:
    canonical, physical = _vectors()[:2]
    assert canonical["source_digest"] != physical["source_digest"]
    assert canonical["parsed_digest"] == physical["parsed_digest"]


def test_semantic_body_change_changes_parsed_value() -> None:
    canonical, _, semantic = _vectors()[:3]
    assert canonical["parsed_digest"] != semantic["parsed_digest"]


def test_bundle_relations_expose_self_describing_digests(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text(
        "---\ntype: Note\ntitle: Olá\n---\nBody\n",
        encoding="utf-8",
    )
    bundle = load_bundle(tmp_path)
    [row] = (
        bundle.concepts.select("source_digest", "parsed_digest").execute().to_dict(orient="records")
    )
    assert row["source_digest"].startswith("sha256:")
    assert row["parsed_digest"].startswith("okf-parsed-v1-jcs-sha256:")
