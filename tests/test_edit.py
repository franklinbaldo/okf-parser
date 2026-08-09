"""Regression tests for conflict-safe single-concept editing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from okf_parser import load_bundle
from okf_parser.edit import EditError, preview_concept_edit, write_concept_edit


def _source_digest(root: Path, concept_id: str) -> str:
    bundle = load_bundle(root)
    row = (
        bundle.concepts.filter(bundle.concepts.concept_id == concept_id)
        .select("source_digest")
        .execute()
    )
    return str(row.iloc[0]["source_digest"])


def test_preview_does_not_write_and_reports_candidate_digests(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    original = "---\ntype: Note\ntitle: Hello\n---\nOld body\n"
    source.write_text(original, encoding="utf-8")
    expected = _source_digest(tmp_path, "note")

    result = preview_concept_edit(
        str(tmp_path),
        "note",
        "New body\n",
        expected,
    )

    assert result["succeeded"] is True
    assert result["written"] is False
    assert result["changed"] is True
    assert result["source_digest"] == expected
    assert result["candidate_source_digest"] != expected
    assert str(result["candidate_parsed_digest"]).startswith("okf-parsed-v1-jcs-sha256:")
    assert source.read_text(encoding="utf-8") == original


def test_write_replaces_only_body_and_preserves_physical_frontmatter_style(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    original_prefix = (
        b"\xef\xbb\xbf---\r\n"
        b"type: Note\r\n"
        b"title: 'Hello' # authored quote/comment\r\n"
        b"description: keep me\r\n"
        b"---\r\n"
    )
    source.write_bytes(original_prefix + b"Old body\r\n")
    expected = _source_digest(tmp_path, "note")

    result = write_concept_edit(
        str(tmp_path),
        "note",
        "New body\nSecond line\n",
        expected,
    )

    assert result["succeeded"] is True
    assert result["written"] is True
    assert result["changed"] is True
    assert source.read_bytes() == original_prefix + b"New body\r\nSecond line\r\n"
    assert _source_digest(tmp_path, "note") == result["candidate_source_digest"]


def test_stale_source_digest_fails_closed_without_touching_file(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("---\ntype: Note\ntitle: Hello\n---\nOriginal\n", encoding="utf-8")
    stale = _source_digest(tmp_path, "note")
    source.write_text("---\ntype: Note\ntitle: Hello\n---\nConcurrent edit\n", encoding="utf-8")
    concurrent = source.read_text(encoding="utf-8")

    result = write_concept_edit(
        str(tmp_path),
        "note",
        "Browser edit\n",
        stale,
    )

    assert result["succeeded"] is False
    assert result["written"] is False
    assert result["conflict_paths"] == ["note.md"]
    assert result["error"] == "concept source changed since it was read"
    assert source.read_text(encoding="utf-8") == concurrent


def test_preview_then_external_change_makes_commit_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("---\ntype: Note\ntitle: Hello\n---\nOriginal\n", encoding="utf-8")
    opened = _source_digest(tmp_path, "note")

    preview = preview_concept_edit(str(tmp_path), "note", "Browser edit\n", opened)
    assert preview["succeeded"] is True
    assert preview["written"] is False

    source.write_text("---\ntype: Note\ntitle: Hello\n---\nExternal edit\n", encoding="utf-8")
    external = source.read_text(encoding="utf-8")

    commit = write_concept_edit(str(tmp_path), "note", "Browser edit\n", opened)
    assert commit["succeeded"] is False
    assert commit["conflict_paths"] == ["note.md"]
    assert source.read_text(encoding="utf-8") == external


def test_noop_preview_is_stable(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("---\ntype: Note\ntitle: Hello\n---\nSame\n", encoding="utf-8")
    expected = _source_digest(tmp_path, "note")

    result = preview_concept_edit(str(tmp_path), "note", "Same\n", expected)

    assert result["succeeded"] is True
    assert result["written"] is False
    assert result["changed"] is False
    assert result["source_digest"] == expected
    assert result["candidate_source_digest"] == expected
    assert str(result["candidate_parsed_digest"]).startswith("okf-parsed-v1-jcs-sha256:")


def test_rejects_a_body_that_cannot_be_encoded_as_utf8(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("---\ntype: Note\ntitle: Hello\n---\nSame\n", encoding="utf-8")
    expected = _source_digest(tmp_path, "note")

    with pytest.raises(EditError, match="UTF-8"):
        preview_concept_edit(str(tmp_path), "note", "bad \ud800 body", expected)


def test_edit_uses_canonical_linear_frontmatter_boundaries(tmp_path: Path) -> None:
    concept = tmp_path / "note.md"
    concept.write_bytes(b"\xef\xbb\xbf---   \r\ntype: Note\r\ntitle: Kept\r\n---\t\r\nold\r\n")
    source = load_bundle(tmp_path).concepts.execute().iloc[0]["source_digest"]

    result = write_concept_edit(str(tmp_path), "note", "new\n", str(source))

    assert result["succeeded"] is True
    assert result["written"] is True
    written = concept.read_bytes()
    assert written.startswith(b"\xef\xbb\xbf---\r\n")
    assert b"title: Kept\r\n" in written
    assert written.endswith(b"---\r\nnew\r\n")
