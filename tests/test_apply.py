"""Tests for `apply`, the RFC 0005 relational frontmatter writer."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import okf_parser.apply as apply_module
from okf_parser.apply import apply_bundle

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from okf_parser.bundle import ValidationReport


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


def test_rename_column_of_an_explicit_null_key_leaves_it_absent(tmp_path: Path) -> None:
    # NULL always means "absent" (RFC 0005's contract), applied uniformly:
    # an authored explicit YAML null carried across a rename compiles to no
    # key at all under either the old or the new name, the same as any other
    # NULL that reaches the final relation, not a literal `prazo_dias: null`.
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
    assert "prazo" not in text.split("---")[1]
    assert "prazo_dias" not in text.split("---")[1]


def test_rename_then_update_to_null_deletes_the_key(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nprazo: 30\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" RENAME COLUMN prazo TO prazo_dias; '
            'UPDATE "Rotina" SET prazo_dias = NULL'
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "prazo" not in text.split("---")[1]
    assert "prazo_dias" not in text.split("---")[1]


def test_chained_renames_compile_to_only_the_final_column(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\na: 30\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" RENAME COLUMN a TO b; '
            'ALTER TABLE "Rotina" RENAME COLUMN b TO c; '
            'UPDATE "Rotina" SET c = c WHERE FALSE'
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "c: '30'" in text or "c: 30" in text
    assert "a:" not in text.split("---")[1]
    assert "b:" not in text.split("---")[1]


def test_alter_on_one_type_then_update_on_another_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\na: 30\n---\n# R1\n")
    _write(tmp_path / "o1.md", "---\ntype: Outro\nx: 1\n---\n# O1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=('ALTER TABLE "Rotina" RENAME COLUMN a TO b; UPDATE "Outro" SET x = \'2\''),
        write=True,
    )

    assert result["succeeded"] is False
    assert "touched more than one type" in _error(result)


def test_canceling_alters_on_one_type_do_not_block_an_update_on_another(tmp_path: Path) -> None:
    # a->b->a nets to Rotina's exact original schema and rows: the compiled
    # result depends only on the final relational state, not the sequence of
    # statements that produced it, so a no-op pair on one type must not be
    # mistaken for "this script touched two types."
    _write(tmp_path / "r1.md", "---\ntype: Rotina\na: 30\n---\n# R1\n")
    _write(tmp_path / "o1.md", "---\ntype: Outro\nx: 1\n---\n# O1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" RENAME COLUMN a TO b; '
            'ALTER TABLE "Rotina" RENAME COLUMN b TO a; '
            "UPDATE \"Outro\" SET x = '2'"
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    assert _changed_paths(result) == ["o1.md"]
    assert "x: '2'" in _read(tmp_path / "o1.md") or "x: 2" in _read(tmp_path / "o1.md")
    assert "a: 30" in _read(tmp_path / "r1.md")


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


def test_add_column_cannot_reintroduce_a_structured_field_name(tmp_path: Path) -> None:
    # "tags" is excluded from the writable namespace because it's a list on
    # r2 - an ADD COLUMN of the same name must not create a second, ordinary
    # "tags" column that the compiler would then use to overwrite or delete
    # the original structured value.
    _write(tmp_path / "r1.md", "---\ntype: Rotina\n---\n# R1\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\ntags:\n  - a\n  - b\n---\n# R2\n")

    result = apply_bundle(
        str(tmp_path),
        sql=('ALTER TABLE "Rotina" ADD COLUMN tags VARCHAR; UPDATE "Rotina" SET tags = \'x\''),
        write=True,
    )

    assert result["succeeded"] is False
    assert "structured" in _error(result)
    assert "- a" in _read(tmp_path / "r2.md")


def test_update_on_one_row_does_not_touch_an_unrelated_rows_null_field(tmp_path: Path) -> None:
    # r2's WHERE never matches, so nothing about r2 should change - not even
    # canonicalizing its authored `campo: null` to absence, which would be
    # an incidental side effect of recompiling every row of the touched
    # type rather than only the ones the script actually changed.
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: GAB\n---\n# R1\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\nsetor: OTHER\ncampo:\n---\n# R2\n")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is True, result
    assert _changed_paths(result) == ["r1.md"]
    assert _read(tmp_path / "r2.md") == "---\ntype: Rotina\nsetor: OTHER\ncampo:\n---\n# R2\n"


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


def test_adding_an_okf_prefixed_column_is_rejected_case_insensitively(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\n---\n# R1\n")
    original = _read(tmp_path / "r1.md")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" ADD COLUMN "__OKF_custom" VARCHAR; '
            "UPDATE \"Rotina\" SET __OKF_custom = 'x'"
        ),
        write=True,
    )

    assert result["succeeded"] is False
    assert "__okf_" in _error(result)
    assert _read(tmp_path / "r1.md") == original


def test_a_structured_okf_prefixed_key_is_rejected_too(tmp_path: Path) -> None:
    # __okf_custom is structured here (a list), so it never becomes a
    # writable column at all - but it's still a collision with the reserved
    # prefix and must be refused up front, not silently hidden behind the
    # internal column of the same name.
    _write(
        tmp_path / "r1.md",
        "---\ntype: Rotina\n__okf_custom:\n  - a\n  - b\n---\n# R1\n",
    )
    original = _read(tmp_path / "r1.md")

    result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET __okf_path = __okf_path WHERE FALSE',
    )

    assert result["succeeded"] is False
    assert "__okf_" in _error(result)
    assert _read(tmp_path / "r1.md") == original


def test_alter_column_type_change_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: GAB\n---\n# R1\n")
    original = _read(tmp_path / "r1.md")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" ALTER COLUMN setor SET DATA TYPE INTEGER USING 0; '
            'UPDATE "Rotina" SET __okf_path = __okf_path WHERE FALSE'
        ),
        write=True,
    )

    assert result["succeeded"] is False
    assert "changed an existing column's type" in _error(result)
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

    real_validate_path = apply_module.validate_path

    def racing_validate_path(
        path: Path,
        exclude: Sequence[str] = (),
        require_spec: str | None = None,
        *,
        normative_spec: bool = False,
    ) -> ValidationReport:
        # `validate_path` runs the baseline check once, right after
        # `_snapshot_bundle` has already captured r2.md's signature, and
        # well before apply re-walks the whole bundle right before writing
        # - mutating r2.md right here simulates a concurrent editor saving
        # it somewhere in that window.
        (tmp_path / "r2.md").write_text(
            "---\ntype: Rotina\nsetor: RACED\n---\n# R2\n", encoding="utf-8"
        )
        return real_validate_path(path, exclude, require_spec, normative_spec=normative_spec)

    monkeypatch.setattr(apply_module, "validate_path", racing_validate_path)

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is False
    assert "r2.md" in _str_list(result, "conflict_paths")
    assert "setor: GAB" in _read(tmp_path / "r1.md")


def test_document_edited_right_after_materialization_read_is_still_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read that feeds the SQL diff is bracketed by two consistency stats.

    r2 changes to `setor: GAB` immediately after `apply` reads its bytes for
    materialization - if the freshness signature were derived from a
    `stat()` made separately from that read, a same-timing coincidence could
    let the stale read go unnoticed. Requiring the stat taken right before
    the read to match the one taken right after catches the drift the
    instant it happens, rather than only much later at write time.
    """
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: GAB\n---\n# R1\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\nsetor: OTHER\n---\n# R2\n")

    real_read_bytes = Path.read_bytes
    mutated = {"done": False}

    def racing_read(self: Path) -> bytes:
        raw = real_read_bytes(self)
        if self.name == "r2.md" and not mutated["done"]:
            mutated["done"] = True
            (tmp_path / "r2.md").write_text(
                "---\ntype: Rotina\nsetor: GAB\n---\n# R2\n", encoding="utf-8"
            )
        return raw

    monkeypatch.setattr(Path, "read_bytes", racing_read)

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is False
    assert "changed while apply was reading it" in _error(result)
    assert "setor: GAB" in _read(tmp_path / "r1.md")


def test_same_size_replace_during_read_is_still_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-length swap between the two consistency stats is not invisible.

    Deriving the manifest signature's size component from `len(raw)` (the
    bytes actually read) instead of a separate `stat()` would let a
    same-size replace slip through undetected: the recorded size would
    still match reality even though the content differs. Bracketing the
    read with two identical `stat()` calls catches this by mtime alone,
    independent of whether the replacement happens to be the same length.
    """
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: AAAAA\n---\n# R1\n")

    real_read_bytes = Path.read_bytes
    mutated = {"done": False}

    def racing_read(self: Path) -> bytes:
        raw = real_read_bytes(self)
        if self.name == "r1.md" and not mutated["done"]:
            mutated["done"] = True
            (tmp_path / "r1.md").write_text(
                "---\ntype: Rotina\nsetor: BBBBB\n---\n# R1\n", encoding="utf-8"
            )
        return raw

    monkeypatch.setattr(Path, "read_bytes", racing_read)

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = 'CCCCC' WHERE setor = 'AAAAA'",
        write=True,
    )

    assert result["succeeded"] is False
    assert "changed while apply was reading it" in _error(result)


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


def test_type_named_like_the_staging_variable_does_not_shadow_a_later_type(
    tmp_path: Path,
) -> None:
    # A type literally named `okf_apply_stage_table` (materialized first,
    # types are processed in sorted order) must not interfere with the
    # later-materialized "zzzz" type - there's no intermediate staging
    # relation under any name at all anymore for a real `type` to shadow.
    _write(
        tmp_path / "a.md",
        "---\ntype: okf_apply_stage_table\nx: from-stage-type\n---\n# A\n",
    )
    _write(tmp_path / "z.md", "---\ntype: zzzz\nx: from-zzzz\n---\n# Z\n")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"zzzz\" SET x = 'updated' WHERE x = 'from-zzzz'",
        write=True,
    )

    assert result["succeeded"] is True, result
    assert _changed_paths(result) == ["z.md"]
    assert "x: updated" in _read(tmp_path / "z.md") or "x: 'updated'" in _read(tmp_path / "z.md")
    assert "x: from-stage-type" in _read(tmp_path / "a.md")


def test_candidate_is_built_from_snapshot_bytes_not_a_transient_live_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient edit that reverts before the final hash check must not leak in.

    1. `_snapshot_bundle` reads A (with `note: keep-me-A`).
    2. Before the final hash recheck, the file is briefly B (`note:
       keep-me-B`), then reverted to A.
    3. The final hash check sees A again and lets the write through.

    If the candidate were rendered from a *second* live read anywhere in
    between (instead of exclusively from the bytes step 1 already
    captured), it could have caught B in that window and published a
    transformed B - which never belonged to the snapshot the migration was
    validated against - while `note` still reads `keep-me-A` in the final
    output only if the candidate genuinely never went back to the
    filesystem for content.
    """
    _write(
        tmp_path / "r1.md",
        "---\ntype: Rotina\nsetor: GAB\nnote: keep-me-A\n---\n# R1\n",
    )

    real_validate_path = apply_module.validate_path
    calls = {"count": 0}

    def racing_validate_path(
        path: Path,
        exclude: Sequence[str] = (),
        require_spec: str | None = None,
        *,
        normative_spec: bool = False,
    ) -> ValidationReport:
        calls["count"] += 1
        if calls["count"] == 1:  # right after the baseline check, before the
            # candidate is built - the exact window a fresh read of the real
            # path (instead of the snapshot's bytes) would have been vulnerable
            # to, before the fix.
            (tmp_path / "r1.md").write_text(
                "---\ntype: Rotina\nsetor: GAB\nnote: keep-me-B\n---\n# R1\n",
                encoding="utf-8",
            )
            (tmp_path / "r1.md").write_text(
                "---\ntype: Rotina\nsetor: GAB\nnote: keep-me-A\n---\n# R1\n",
                encoding="utf-8",
            )
        return real_validate_path(path, exclude, require_spec, normative_spec=normative_spec)

    monkeypatch.setattr(apply_module, "validate_path", racing_validate_path)

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "setor: '#GAB#FSB'" in text or "setor: #GAB#FSB" in text
    assert "note: keep-me-A" in text


def test_update_selecting_an_already_null_field_deletes_it(tmp_path: Path) -> None:
    # `campo` is NULL both before and after: a before/after value comparison
    # alone can't tell "this row was selected and explicitly set to NULL"
    # from "this row was never selected" - RETURNING resolves the ambiguity.
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: GAB\ncampo:\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = 'NOVO', campo = NULL WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "setor: NOVO" in text
    assert "campo" not in text.split("---")[1]


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
