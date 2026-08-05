"""Tests for `apply`, the RFC 0005 relational frontmatter writer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import okf_parser.apply as apply_module
from okf_parser.apply import apply_bundle

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _error(result: dict[str, object]) -> str:
    error = result["error"]
    assert isinstance(error, str)
    return error


def _str_list(result: dict[str, object], key: str) -> list[str]:
    paths = result[key]
    assert isinstance(paths, list)
    return [str(item) for item in paths]


def _changed_paths(result: dict[str, object]) -> list[str]:
    return _str_list(result, "changed_paths")


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

    # DROP COLUMN acts bundle-wide regardless of the trailing UPDATE's WHERE
    # clause matching no rows: every document that had the field loses it.
    assert result["succeeded"] is True, result
    assert sorted(_changed_paths(result)) == ["r1.md", "r2.md"]
    assert "prazo" not in _read(tmp_path / "r1.md")
    assert "prazo" not in _read(tmp_path / "r2.md")


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


def test_drop_column_deletes_an_explicit_null_key(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nprazo:\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" DROP COLUMN "prazo"; '
            'UPDATE "Rotina" SET __okf_path = __okf_path WHERE FALSE'
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    assert "prazo" not in _read(tmp_path / "r1.md")


def test_rename_column_carries_an_explicit_null_key(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nprazo:\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" RENAME COLUMN "prazo" TO "prazo_dias"; '
            'UPDATE "Rotina" SET __okf_path = __okf_path WHERE FALSE'
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "prazo_dias" in text
    assert "prazo:" not in text.split("---")[1]


def test_field_structured_on_one_document_is_unwritable_on_every_document(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ntags: um-item\n---\n# R1\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\ntags:\n  - a\n  - b\n---\n# R2\n")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET tags = 'x' WHERE tags IS NULL",
        write=True,
    )

    assert result["succeeded"] is False
    assert "script failed" in _error(result)
    assert "tags: um-item" in _read(tmp_path / "r1.md")
    assert "- a" in _read(tmp_path / "r2.md")


def test_adding_a_reserved_okf_prefixed_column_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\n---\n# R1\n")
    original = _read(tmp_path / "r1.md")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" ADD COLUMN "__okf_custom" VARCHAR; '
            "UPDATE \"Rotina\" SET __okf_custom = 'x'"
        ),
        write=True,
    )

    assert result["succeeded"] is False
    assert "__okf_" in _error(result)
    assert _read(tmp_path / "r1.md") == original


def test_untouched_file_edited_before_write_aborts_as_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply re-checks the whole bundle, not just the fields it's writing.

    It compares the state of every file it saw during validation against the
    state immediately before replacing any real file, and refuses to write
    anything if the bundle moved since. r2 never matches the WHERE clause
    below, so this exercises that guarantee for a document the SQL itself
    never touches, not just the ones it does.
    """
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: GAB\n---\n# R1\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\nsetor: OTHER\n---\n# R2\n")

    real_signature = apply_module._file_signature  # noqa: SLF001
    seen_r2 = {"count": 0}

    def racing_signature(path: Path) -> tuple[int, int]:
        # Simulates a concurrent editor saving r2.md right after `apply`
        # takes its first (baseline) look at it, so the second (pre-write)
        # look sees a bundle that has moved on. The baseline signature is
        # captured *before* the race so it reflects the pre-race content.
        if path.name != "r2.md":
            return real_signature(path)
        seen_r2["count"] += 1
        signature = real_signature(path)
        if seen_r2["count"] == 1:
            (tmp_path / "r2.md").write_text(
                "---\ntype: Rotina\nsetor: RACED\n---\n# R2\n", encoding="utf-8"
            )
        return signature

    monkeypatch.setattr(apply_module, "_file_signature", racing_signature)

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is False
    assert "r2.md" in _str_list(result, "conflict_paths")
    assert "setor: GAB" in _read(tmp_path / "r1.md")


def test_type_named_like_the_internal_before_namespace_works_like_any_other_type(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "r1.md",
        '---\ntype: "__okf_before__Rotina"\nsetor: GAB\n---\n# R1\n',
    )

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"__okf_before__Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "setor: '#GAB#FSB'" in text or "setor: #GAB#FSB" in text


def test_write_preserves_bom_and_crlf(tmp_path: Path) -> None:
    path = tmp_path / "r1.md"
    path.write_bytes(
        b"\xef\xbb\xbf---\r\ntype: Rotina\r\nsetor: GAB\r\n---\r\n# R1\r\n\r\nBody.\r\n"
    )

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is True, result
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("utf-8")
    assert "setor: '#GAB#FSB'" in text or "setor: #GAB#FSB" in text
