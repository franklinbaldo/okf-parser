"""Tests that exclusion reaches discovery, validation, formatting and the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb
import pytest

from okf_parser.bundle import validate_path
from okf_parser.formatting import format_path
from okf_parser.ingestion import discover, ingest_documents
from okf_parser.service import check_bundle, export_duckdb

if TYPE_CHECKING:
    from pathlib import Path

EXCLUSION_FILENAME = ".okfignore"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _mixed_repository(root: Path) -> None:
    """Build the shape from the issue: OKF bundles beside code and vendored docs."""
    _write(root / "README.md", "# Project\n")
    _write(root / "CLAUDE.md", "# Agent notes\n")
    _write(root / "vendor" / "dep" / "guide.md", "# Vendored\n")
    _write(
        root / "equipe" / "fulano.md",
        "---\ntype: Membro da Equipe\ntitle: Fulano\n---\n\n# Fulano\n",
    )
    _write(
        root / "items" / "tarefa.md",
        "---\ntype: Work Item\ntitle: Tarefa\n---\n\n# Tarefa\n\n[Fulano](/equipe/fulano.md)\n",
    )


def _relatives(root: Path, exclude: tuple[str, ...] = ()) -> list[str]:
    return [path.relative_to(root).as_posix() for path in discover(root, exclude)]


def test_discovery_excludes_a_directory_and_keeps_path_order(tmp_path: Path) -> None:
    _mixed_repository(tmp_path)

    assert _relatives(tmp_path, ("vendor",)) == [
        "CLAUDE.md",
        "README.md",
        "equipe/fulano.md",
        "items/tarefa.md",
    ]


def test_discovery_without_rules_is_unchanged(tmp_path: Path) -> None:
    _mixed_repository(tmp_path)

    assert len(discover(tmp_path)) == 5


def test_discovery_skips_tool_directories_as_a_load_does(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "# A\n")
    _write(tmp_path / "node_modules" / "pkg" / "README.md", "# Dependency\n")
    _write(tmp_path / ".venv" / "lib" / "notes.md", "# Environment\n")

    assert _relatives(tmp_path) == ["a.md"]
    assert [item.path for item in ingest_documents(tmp_path)] == ["a.md"]


def test_the_file_keeps_comments_blank_lines_and_trailing_slashes_gitignore_style(
    tmp_path: Path,
) -> None:
    _mixed_repository(tmp_path)
    _write(tmp_path / EXCLUSION_FILENAME, "# vendored dependencies\nvendor/\n\n  \n/*.md   \n")

    assert _relatives(tmp_path) == ["equipe/fulano.md", "items/tarefa.md"]


def test_command_line_patterns_extend_the_file(tmp_path: Path) -> None:
    _mixed_repository(tmp_path)
    _write(tmp_path / EXCLUSION_FILENAME, "vendor\n")

    assert _relatives(tmp_path, ("/*.md",)) == ["equipe/fulano.md", "items/tarefa.md"]


def test_an_unreadable_exclusion_file_names_the_path(tmp_path: Path) -> None:
    """Silently ignoring a corrupt ignore file would validate the wrong tree."""
    _write(tmp_path / "a.md", "# A\n")
    (tmp_path / EXCLUSION_FILENAME).write_bytes(b"vendor\n\xff\xfe\n")

    with pytest.raises(ValueError, match=r"\.okfignore"):
        list(ingest_documents(tmp_path))


def test_a_single_string_is_rejected_rather_than_split_into_letters(tmp_path: Path) -> None:
    """`exclude="vendor"` type-checks as a sequence and would exclude nothing."""
    with pytest.raises(TypeError):
        list(ingest_documents(tmp_path, exclude="vendor"))


def test_the_mixed_repository_validates_from_its_real_root(tmp_path: Path) -> None:
    """The issue: excluding noise is what lets cross-bundle links resolve.

    Checking each bundle separately makes the link an OKF101 even though the
    target exists, because the target sits outside the checked root.
    """
    _mixed_repository(tmp_path)
    fragmented = validate_path(tmp_path / "items")
    assert [item.code for item in fragmented.violations] == ["OKF101"]

    _write(tmp_path / EXCLUSION_FILENAME, "vendor\n/*.md\n")
    whole = validate_path(tmp_path)

    assert whole.is_conformant
    assert whole.violations == ()
    assert whole.concept_count == 2


def test_an_excluded_file_is_never_rewritten(tmp_path: Path) -> None:
    """`format --write` on a repository root must not reformat dependencies."""
    original = "# Vendored\n\n-   untouched\n"
    _write(tmp_path / "vendor" / "dep.md", original)
    _write(tmp_path / "a.md", "# Mine\n\n-   item\n")
    _write(tmp_path / EXCLUSION_FILENAME, "vendor\n")

    report = format_path(tmp_path, write=True)

    assert (tmp_path / "vendor" / "dep.md").read_text(encoding="utf-8") == original
    assert report.changed_paths == ("a.md",)
    assert report.markdown_count == 1


def test_check_accepts_repeated_exclude_options(tmp_path: Path) -> None:
    _mixed_repository(tmp_path)

    payload = check_bundle(str(tmp_path), exclude=["vendor", "/*.md"])

    assert payload["conformant"]
    assert payload["concept_count"] == 2


def test_format_accepts_repeated_exclude_options(tmp_path: Path) -> None:
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "dep.md").write_text("# Dep\n\n-   item\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "doc.md").write_text("# Doc\n\n-   item\n", encoding="utf-8")

    report = format_path(tmp_path, exclude=["vendor", "docs"])

    assert report.markdown_count == 0


def test_duckdb_export_honours_exclusion(tmp_path: Path) -> None:
    """Materializing a mixed repository must not import its unrelated Markdown."""
    _mixed_repository(tmp_path)
    database = tmp_path / "knowledge.duckdb"

    payload = export_duckdb(
        str(tmp_path),
        str(database),
        exclude=["vendor", "/*.md"],
    )

    assert payload["markdown_count"] == 2
    assert payload["concept_count"] == 2
    connection = duckdb.connect(str(database), read_only=True)
    try:
        paths = connection.sql("SELECT path FROM okf.concepts ORDER BY path").fetchall()
    finally:
        connection.close()
    assert paths == [("equipe/fulano.md",), ("items/tarefa.md",)]


def test_the_exclusion_file_itself_needs_no_pattern(tmp_path: Path) -> None:
    """`.okfignore` is not Markdown, so it never reaches discovery."""
    _write(tmp_path / "a.md", "---\ntype: Note\n---\n\n# A\n")
    _write(tmp_path / EXCLUSION_FILENAME, "vendor\n")

    assert validate_path(tmp_path).markdown_count == 1


def test_a_negation_re_includes_knowledge_inside_an_excluded_directory(tmp_path: Path) -> None:
    """The monorepo case: vendored code is noise, the knowledge inside it is not.

    Under `.gitignore` semantics git could not express this, because it prunes
    the walk and never reconsiders. Here the walk descends whenever a negation
    exists, so the re-inclusion is real rather than a silent no-op.
    """
    _write(tmp_path / "vendor" / "dep" / "guide.md", "# Guide\n")
    _write(
        tmp_path / "vendor" / "knowledge" / "tarefa.md",
        "---\ntype: Work Item\n---\n\n# Tarefa\n",
    )
    _write(tmp_path / EXCLUSION_FILENAME, "vendor\n!vendor/knowledge\n")

    report = validate_path(tmp_path)

    assert report.is_conformant
    assert report.markdown_count == 1
    assert report.concept_count == 1
    assert _relatives(tmp_path) == ["vendor/knowledge/tarefa.md"]
