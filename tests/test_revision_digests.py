"""Cross-language deterministic source and parsed-revision identity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from okf_parser import load_bundle
from okf_parser.parser import parse_document_text
from okf_parser.service import inventory_bundle


def _vectors() -> list[dict[str, str]]:
    path = Path(__file__).parents[1] / "conformance" / "revision-digests.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return cast("list[dict[str, str]]", payload["cases"])


def test_revision_digest_vectors_are_pinned() -> None:
    for case in _vectors():
        parsed = parse_document_text(Path(f"{case['name']}.md"), case["source"])
        assert parsed.source_digest == case["source_digest"]
        assert parsed.revision_digest == case["revision_digest"]


def test_physical_variant_changes_source_not_revision() -> None:
    canonical, physical = _vectors()[:2]

    assert canonical["source_digest"] != physical["source_digest"]
    assert canonical["revision_digest"] == physical["revision_digest"]


def test_semantic_change_changes_revision() -> None:
    canonical, _, semantic = _vectors()

    assert canonical["revision_digest"] != semantic["revision_digest"]


def test_inventory_can_expose_serialized_revision_identity(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("---\ntype: Note\n---\nBody\n", encoding="utf-8")

    payload = inventory_bundle(str(tmp_path), revisions=True)

    [revision] = cast("list[dict[str, str]]", payload["revisions"])
    assert revision["concept_id"] == "note"
    assert revision["path"] == "note.md"
    assert revision["source_digest"].startswith("sha256:")
    assert revision["revision_digest"].startswith("okf-revision-v1-sha256:")


def test_bundle_relations_expose_self_describing_digests(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text(
        "---\ntype: Note\ntitle: Olá\n---\nBody\n",
        encoding="utf-8",
    )

    bundle = load_bundle(tmp_path)
    [row] = (
        bundle.concepts.select("source_digest", "revision_digest")
        .execute()
        .to_dict(orient="records")
    )

    assert row["source_digest"].startswith("sha256:")
    assert row["revision_digest"].startswith("okf-revision-v1-sha256:")
