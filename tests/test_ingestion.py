"""Capability-driven ingestion tests."""

from pathlib import Path

from okf_parser import IngestionCapability, ingest_documents


def test_identity_projection_does_not_require_valid_content(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_bytes(b"\xff")
    (tmp_path / "a.md").write_text("---\nunterminated", encoding="utf-8")
    envelopes = list(ingest_documents(tmp_path))

    assert [item.path for item in envelopes] == ["a.md", "b.md"]
    assert [item.ordinal for item in envelopes] == [0, 1]
    assert all(item.error is None for item in envelopes)
    assert all(item.body is None and item.frontmatter is None for item in envelopes)


def test_projected_ingestion_reads_once_and_reuses_document_facts(tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text(
        "---\ntype: Note\ntitle: One\n---\n# Heading\n\n[Two](two.md)\n",
        encoding="utf-8",
    )

    [item] = list(
        ingest_documents(
            tmp_path,
            (
                IngestionCapability.FRONTMATTER,
                IngestionCapability.MARKDOWN_FACTS,
            ),
        )
    )

    assert item.classification == "typed:Note"
    assert item.frontmatter == {"type": "Note", "title": "One"}
    assert item.body is None
    assert item.facts is not None
    assert item.facts.links == ("two.md",)
    assert item.facts.headings == ((1, "Heading"),)
