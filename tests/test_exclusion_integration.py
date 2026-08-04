"""Tests that exclusion reaches discovery, validation, formatting and the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb

from okf_parser.bundle import validate_path
from okf_parser.cli import check as check_command
from okf_parser.cli import format_command
from okf_parser.discovery import discover_markdown
from okf_parser.exclusion import EXCLUSION_FILENAME, ExclusionRules
from okf_parser.formatting import format_path
from okf_parser.service import export_duckdb

if TYPE_CHECKING:
    from pathlib import Path


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


def test_discovery_prunes_an_excluded_directory(tmp_path: Path) -> None:
    _mixed_repository(tmp_path)
    rules = ExclusionRules(patterns=("vendor",))

    found = discover_markdown(tmp_path, rules)

    relatives = {path.relative_to(tmp_path).as_posix() for path in found}
    assert relatives == {"README.md", "CLAUDE.md", "equipe/fulano.md", "items/tarefa.md"}


def test_discovery_without_rules_is_unchanged(tmp_path: Path) -> None:
    _mixed_repository(tmp_path)

    assert len(discover_markdown(tmp_path)) == 5


def test_the_mixed_repository_validates_from_its_real_root(tmp_path: Path) -> None:
    """The issue: excluding noise is what lets cross-bundle links resolve.

    Checking each bundle separately makes the link an OKF101 even though the
    target exists, because the target sits outside the checked root.
    """
    _mixed_repository(tmp_path)
    fragmented = validate_path(tmp_path / "items")
    assert [item.code for item in fragmented.violations] == ["OKF101"]

    _write(tmp_path / EXCLUSION_FILENAME, "vendor\n*.md\n")
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

    result = check_command(str(tmp_path), exclude=["vendor", "*.md"])

    assert result.exit_code == 0
    assert result.payload["concept_count"] == 2


def test_format_accepts_repeated_exclude_options(tmp_path: Path) -> None:
    _write(tmp_path / "vendor" / "dep.md", "# Vendored\n\n-   untouched\n")
    _write(tmp_path / "a.md", "# Mine\n")

    result = format_command(str(tmp_path), exclude=["vendor"])

    assert result.payload["markdown_count"] == 1


def test_duckdb_export_honours_exclusion(tmp_path: Path) -> None:
    """Materializing a mixed repository must not import its unrelated Markdown."""
    _mixed_repository(tmp_path)
    database = tmp_path / "knowledge.duckdb"

    payload = export_duckdb(
        str(tmp_path),
        str(database),
        exclude=["vendor", "*.md"],
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


def test_okf_metadata_directory_is_never_content(tmp_path: Path) -> None:
    """`.okf/` descreve o bundle e não é conceito dele.

    A disposição proposta pelo issue #25 guarda a especificação de cada tipo em
    `.okf/specs/<tipo>.md`. Enquanto o walk descia ali, o documento que
    descreve um tipo virava conceito do bundle e passava a exigir `type` — e o
    tipo dessas especificações exigiria a sua própria especificação dentro de
    todo bundle que tivesse uma. A pasta é metadado, e por isso é podada.
    """
    _write(tmp_path / "index.md", "# Bundle\n")
    _write(
        tmp_path / "tarefa.md",
        "---\ntype: Work Item\ntitle: Tarefa\n---\n\n# Tarefa\n",
    )
    _write(tmp_path / ".okf" / "specs" / "work-item.md", "# Work Item\n")

    encontrados = {caminho.name for caminho in discover_markdown(tmp_path)}

    assert encontrados == {"index.md", "tarefa.md"}
    assert validate_path(tmp_path).violations == ()
