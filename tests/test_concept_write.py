"""Public structured concept create/patch writes stay parser-owned and conflict-safe."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser import (
    ConceptWriteError,
    load_bundle,
    preview_concept_create,
    preview_concept_patch,
    write_concept_create,
    write_concept_patch,
)

if TYPE_CHECKING:
    from pathlib import Path


def _source_digest(root: Path, concept_id: str) -> str:
    bundle = load_bundle(root)
    row = (
        bundle.concepts.filter(bundle.concepts.concept_id == concept_id)
        .select("source_digest")
        .execute()
    )
    return str(row.iloc[0]["source_digest"])


def _execution_schema(root: Path) -> str:
    specs = root / "specs"
    specs.mkdir(exist_ok=True)
    (specs / "runexecution.schema.sql").write_text(
        'CREATE TABLE "RunExecution" (exit_code INTEGER, changed BOOLEAN);\n',
        encoding="utf-8",
    )
    return "specs/{slug}.md"


def test_create_preview_then_write_round_trips_through_declared_types(tmp_path: Path) -> None:
    template = _execution_schema(tmp_path)
    target = tmp_path / "runs" / "execution.md"
    frontmatter = {
        "type": "RunExecution",
        "id": "run-executions/example",
        "exit_code": 0,
        "changed": False,
    }

    preview = preview_concept_create(
        str(tmp_path),
        "runs/execution.md",
        frontmatter,
        body="# Execution\n",
    )

    assert preview["succeeded"] is True
    assert preview["written"] is False
    assert preview["changed"] is True
    assert not target.exists()

    written = write_concept_create(
        str(tmp_path),
        "runs/execution.md",
        frontmatter,
        body="# Execution\n",
    )

    assert written["succeeded"] is True
    assert written["written"] is True
    assert target.exists()
    with load_bundle(tmp_path).compile_types(template) as typed:
        row = typed["RunExecution"].execute().iloc[0]
        assert row["exit_code"] == 0
        assert isinstance(row["exit_code"], int)
        assert row["changed"] is False
        assert isinstance(row["changed"], bool)


def test_patch_updates_and_removes_frontmatter_without_reimplementing_yaml(tmp_path: Path) -> None:
    template = _execution_schema(tmp_path)
    source = tmp_path / "execution.md"
    source.write_text(
        "---\n"
        "type: RunExecution\n"
        "id: run-executions/example\n"
        "exit_code: 1\n"
        "changed: true\n"
        "obsolete: remove-me\n"
        "---\n"
        "# Execution\n",
        encoding="utf-8",
    )
    expected = _source_digest(tmp_path, "run-executions/example")

    preview = preview_concept_patch(
        str(tmp_path),
        "run-executions/example",
        {"exit_code": 0, "changed": False},
        expected,
        remove=("obsolete",),
    )
    assert preview["succeeded"] is True
    assert preview["written"] is False

    result = write_concept_patch(
        str(tmp_path),
        "run-executions/example",
        {"exit_code": 0, "changed": False},
        expected,
        remove=("obsolete",),
    )

    assert result["succeeded"] is True
    assert result["written"] is True
    parsed = load_bundle(tmp_path).documents_by_type()["RunExecution"][0]
    assert "obsolete" not in parsed.frontmatter
    with load_bundle(tmp_path).compile_types(template) as typed:
        row = typed["RunExecution"].execute().iloc[0]
        assert row["exit_code"] == 0
        assert row["changed"] is False


def test_patch_stale_digest_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("---\ntype: Note\ntitle: First\n---\nBody\n", encoding="utf-8")
    stale = _source_digest(tmp_path, "note")
    source.write_text("---\ntype: Note\ntitle: Concurrent\n---\nBody\n", encoding="utf-8")
    concurrent = source.read_text(encoding="utf-8")

    result = write_concept_patch(str(tmp_path), "note", {"title": "Mine"}, stale)

    assert result["succeeded"] is False
    assert result["written"] is False
    assert result["conflict_paths"] == ["note.md"]
    assert source.read_text(encoding="utf-8") == concurrent


def test_create_rejects_new_normative_diagnostic_before_write(tmp_path: Path) -> None:
    target = tmp_path / "bad.md"

    result = write_concept_create(
        str(tmp_path),
        "bad.md",
        {"type": "Note", "id": "bad"},
        body="[Missing](missing.md)\n",
    )

    assert result["succeeded"] is False
    assert result["written"] is False
    assert result["validation"]
    assert not target.exists()


def test_create_existing_path_and_identity_patch_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("---\ntype: Note\ntitle: Hello\n---\n", encoding="utf-8")
    expected = _source_digest(tmp_path, "note")

    create = write_concept_create(
        str(tmp_path),
        "note.md",
        {"type": "Note", "id": "replacement"},
    )
    assert create["succeeded"] is False
    assert create["conflict_paths"] == ["note.md"]

    with pytest.raises(ConceptWriteError, match="identity fields"):
        preview_concept_patch(str(tmp_path), "note", {"id": "renamed"}, expected)

    with pytest.raises(ConceptWriteError, match="inside the bundle root"):
        preview_concept_create(str(tmp_path), "../escape.md", {"type": "Note"})
