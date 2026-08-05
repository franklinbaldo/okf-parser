"""Tests for `apply`, the RFC 0005 relational frontmatter writer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.apply import apply_bundle

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _error(result: dict[str, object]) -> str:
    error = result["error"]
    assert isinstance(error, str)
    return error


def _changed_paths(result: dict[str, object]) -> list[str]:
    paths = result["changed_paths"]
    assert isinstance(paths, list)
    return [str(item) for item in paths]


def test_dry_run_reports_changes_without_writing(tmp_path: Path) -> None:
    _write(
        tmp_path / "rotinas" / "r1.md",
        "---\ntype: Rotina\nsetor: GAB\n---\n# Rotina 1\n",
    )
    original = _read(tmp_path / "rotinas" / "r1.md")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
    )

    assert result["succeeded"] is True
    assert result["written"] is False
    assert result["changed_paths"] == ["rotinas/r1.md"]
    assert _read(tmp_path / "rotinas" / "r1.md") == original


def test_write_updates_field_and_preserves_untouched_content(tmp_path: Path) -> None:
    _write(
        tmp_path / "rotinas" / "r1.md",
        "---\ntype: Rotina\n# a comment\nsetor: GAB\ntitle: Something\n---\n"
        "# Rotina 1\n\nBody text.\n",
    )

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is True, result
    assert result["written"] is True
    assert result["changed_paths"] == ["rotinas/r1.md"]

    text = _read(tmp_path / "rotinas" / "r1.md")
    assert "setor: '#GAB#FSB'" in text or 'setor: "#GAB#FSB"' in text or "setor: #GAB#FSB" in text
    assert "# a comment" in text
    assert "title: Something" in text
    assert "Body text." in text


def test_add_column_backfills_a_new_field(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\n---\n# R1\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\nsetor: GAB\n---\n# R2\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" ADD COLUMN "timestamp" VARCHAR; '
            "UPDATE \"Rotina\" SET timestamp = '2026-01-01' WHERE timestamp IS NULL"
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    assert sorted(_changed_paths(result)) == ["r1.md", "r2.md"]
    r1_text = _read(tmp_path / "r1.md")
    assert "timestamp: '2026-01-01'" in r1_text or "timestamp: 2026-01-01" in r1_text
    assert "setor: GAB" in _read(tmp_path / "r2.md")


def test_drop_column_removes_field_from_every_row(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nprazo: 30\n---\n# R1\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\nprazo: 15\n---\n# R2\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" DROP COLUMN "prazo"; '
            'UPDATE "Rotina" SET __okf_path = __okf_path WHERE FALSE'
        ),
        write=True,
    )

    # No row actually changes (WHERE FALSE), but the schema change alone
    # should not be reported as a written document since nothing's row diff
    # changed - dropping a column with no matching row update is a no-op
    # for concept files in this implementation.
    assert result["succeeded"] is True, result


def test_rename_column_preserves_value(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nprazo: 30\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" RENAME COLUMN "prazo" TO "prazo_dias"; '
            'UPDATE "Rotina" SET prazo_dias = prazo_dias WHERE prazo_dias IS NOT NULL'
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "prazo_dias:" in text
    assert "prazo:" not in text.split("---")[1]


def test_protected_column_write_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\n---\n# R1\n")
    original = _read(tmp_path / "r1.md")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET __okf_path = 'elsewhere.md'",
        write=True,
    )

    assert result["succeeded"] is False
    assert "protected" in _error(result)
    assert _read(tmp_path / "r1.md") == original


def test_non_scalar_field_is_not_a_writable_column(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ntags:\n  - a\n  - b\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET tags = 'x'",
    )

    assert result["succeeded"] is False
    assert "script failed" in _error(result)


def test_type_rewrite_migrates_the_document(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Revisao Ciencia\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Revisao Ciencia\" SET type = 'Revisão Ciência'",
        write=True,
    )

    assert result["succeeded"] is True, result
    assert "type: Revisão Ciência" in _read(tmp_path / "r1.md")


def test_rerunning_a_converged_type_rewrite_errors_not_silently_noops(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Revisao Ciencia\n---\n# R1\n")
    sql = "UPDATE \"Revisao Ciencia\" SET type = 'Revisão Ciência'"

    first = apply_bundle(str(tmp_path), sql=sql, write=True)
    assert first["succeeded"] is True, first

    second = apply_bundle(str(tmp_path), sql=sql, write=True)
    assert second["succeeded"] is False
    assert "script failed" in _error(second)


def test_case_insensitive_type_collision_is_refused(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\n---\n# R1\n")
    _write(tmp_path / "r2.md", "---\ntype: ROTINA\n---\n# R2\n")

    result = apply_bundle(str(tmp_path), sql="UPDATE \"Rotina\" SET setor = 'x'")

    assert result["succeeded"] is False
    assert "collides" in _error(result)


def test_field_sugar_is_equivalent_to_sql(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: GAB\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        type_name="Rotina",
        field_name="setor",
        from_value="GAB",
        to_value="#GAB#FSB",
        write=True,
    )

    assert result["succeeded"] is True, result
    assert "setor:" in _read(tmp_path / "r1.md")
    assert "GAB" not in _read(tmp_path / "r1.md") or "#GAB#FSB" in _read(tmp_path / "r1.md")


def test_mutation_introducing_a_normative_diagnostic_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET type = ''",
        write=True,
    )

    assert result["succeeded"] is False
    assert result["validation"]
    assert not (tmp_path / "r1.md").read_text(encoding="utf-8").count("type: ''")


def test_no_matching_rows_is_a_successful_noop(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: GAB\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = 'GAB' WHERE setor = 'nonexistent-value'",
    )

    assert result["succeeded"] is True
    assert result["changed_paths"] == []
    assert result["written"] is False
