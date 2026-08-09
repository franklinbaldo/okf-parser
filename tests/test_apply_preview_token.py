"""Reviewed-preview token tests for RFC 0005 apply."""

# ruff: noqa: PLC0415

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.apply import apply_bundle

if TYPE_CHECKING:
    from pathlib import Path


def _write_bundle(root: Path) -> None:
    (root / "note.md").write_text(
        """---
type: Note
status: todo
---
Body
""",
        encoding="utf-8",
    )
    (root / "other.md").write_text(
        """---
type: Other
title: Other
---
Other body
""",
        encoding="utf-8",
    )


def _status_sql() -> str:
    return "UPDATE \"Note\" SET status = 'done' WHERE __okf_concept_id = 'note'"


def test_preview_returns_a_deterministic_candidate_token(tmp_path: Path) -> None:
    _write_bundle(tmp_path)

    first = apply_bundle(str(tmp_path), sql=_status_sql())
    second = apply_bundle(str(tmp_path), sql=_status_sql())

    token = first["preview_token"]
    assert isinstance(token, str)
    assert token.startswith("okf-apply-preview-v1-sha256:")
    assert second["preview_token"] == token
    assert first["changed_paths"] == ["note.md"]
    note = (tmp_path / "note.md").read_text(encoding="utf-8")
    assert "status: todo" in note


def test_write_accepts_the_exact_reviewed_preview(tmp_path: Path) -> None:
    _write_bundle(tmp_path)
    preview = apply_bundle(str(tmp_path), sql=_status_sql())
    token = preview["preview_token"]
    assert isinstance(token, str)

    result = apply_bundle(
        str(tmp_path),
        sql=_status_sql(),
        write=True,
        expected_preview_token=token,
    )

    assert result["succeeded"] is True
    assert result["written"] is True
    assert result["preview_token"] == token
    assert "status: done" in (tmp_path / "note.md").read_text(encoding="utf-8")


def test_wrong_preview_token_fails_closed(tmp_path: Path) -> None:
    _write_bundle(tmp_path)
    before = (tmp_path / "note.md").read_text(encoding="utf-8")

    result = apply_bundle(
        str(tmp_path),
        sql=_status_sql(),
        write=True,
        expected_preview_token="okf-apply-preview-v1-sha256:" + "0" * 64,
    )

    assert result["succeeded"] is False
    assert result["written"] is False
    assert result["conflict_paths"] == ["note.md"]
    assert result["error"] == "apply candidate no longer matches the reviewed preview"
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == before


def test_bundle_change_after_preview_invalidates_commit(tmp_path: Path) -> None:
    _write_bundle(tmp_path)
    preview = apply_bundle(str(tmp_path), sql=_status_sql())
    token = preview["preview_token"]
    assert isinstance(token, str)

    other = tmp_path / "other.md"
    other.write_text(
        other.read_text(encoding="utf-8") + "External change\n",
        encoding="utf-8",
    )
    note_before = (tmp_path / "note.md").read_text(encoding="utf-8")

    result = apply_bundle(
        str(tmp_path),
        sql=_status_sql(),
        write=True,
        expected_preview_token=token,
    )

    assert result["succeeded"] is False
    assert result["written"] is False
    assert result["error"] == "apply candidate no longer matches the reviewed preview"
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == note_before


def test_nondeterministic_sql_cannot_commit_a_different_candidate(tmp_path: Path) -> None:
    _write_bundle(tmp_path)
    sql = (
        'ALTER TABLE "Note" ADD COLUMN nonce VARCHAR; '
        'UPDATE "Note" SET nonce = CAST(random() AS VARCHAR) '
        "WHERE __okf_concept_id = 'note'"
    )
    preview = apply_bundle(str(tmp_path), sql=sql)
    token = preview["preview_token"]
    assert isinstance(token, str)
    before = (tmp_path / "note.md").read_text(encoding="utf-8")

    result = apply_bundle(
        str(tmp_path),
        sql=sql,
        write=True,
        expected_preview_token=token,
    )

    assert result["succeeded"] is False
    assert result["written"] is False
    assert result["error"] == "apply candidate no longer matches the reviewed preview"
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == before


def test_service_forwards_reviewed_preview_token(tmp_path: Path) -> None:
    from okf_parser.service import apply_bundle as service_apply_bundle

    _write_bundle(tmp_path)
    preview = service_apply_bundle(str(tmp_path), sql=_status_sql())
    token = preview["preview_token"]
    assert isinstance(token, str)

    result = service_apply_bundle(
        str(tmp_path),
        sql=_status_sql(),
        write=True,
        expected_preview_token=token,
    )

    assert result["succeeded"] is True
    assert result["written"] is True
    assert result["preview_token"] == token
