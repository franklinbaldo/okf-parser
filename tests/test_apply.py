"""Tests for `apply`, the RFC 0005 relational frontmatter writer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.apply import apply_bundle

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _error(result: Mapping[str, object]) -> str:
    error = result["error"]
    assert isinstance(error, str)
    return error


def _str_list(result: Mapping[str, object], key: str) -> list[str]:
    paths = result[key]
    assert isinstance(paths, list)
    return [str(item) for item in paths]


def _changed_paths(result: Mapping[str, object]) -> list[str]:
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


def test_flat_list_field_is_writable_and_supports_list_functions(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ntags:\n  - a\n  - b\n---\n# R1\n")

    appended = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET tags = list_append(tags, 'c')",
        write=True,
    )
    assert appended["succeeded"] is True, appended
    assert "- c" in _read(tmp_path / "r1.md")

    replaced = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET tags = ['x']",
        write=True,
    )
    assert replaced["succeeded"] is True, replaced
    text = _read(tmp_path / "r1.md")
    assert "- x" in text
    assert "- a" not in text


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


def test_one_script_can_change_several_types(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\na: 30\n---\n# R1\n")
    _write(tmp_path / "o1.md", "---\ntype: Outro\nx: 1\n---\n# O1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=('ALTER TABLE "Rotina" RENAME COLUMN a TO b; UPDATE "Outro" SET x = \'2\''),
        write=True,
    )

    assert result["succeeded"] is True, result
    assert sorted(_changed_paths(result)) == ["o1.md", "r1.md"]
    assert "b: '30'" in _read(tmp_path / "r1.md")
    assert "x: '2'" in _read(tmp_path / "o1.md")


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
    _write(tmp_path / "r2.md", "---\ntype: Rotina\ntags:\n  - [a, b]\n---\n# R2\n")

    result = apply_bundle(
        str(tmp_path),
        sql=('ALTER TABLE "Rotina" ADD COLUMN tags VARCHAR; UPDATE "Rotina" SET tags = \'x\''),
        write=True,
    )

    assert result["succeeded"] is False
    assert "structured" in _error(result)
    assert "- [a, b]" in _read(tmp_path / "r2.md")


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


def test_a_retyped_column_writes_its_values_as_text(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nnivel: '007'\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql='ALTER TABLE "Rotina" ALTER COLUMN nivel SET DATA TYPE INTEGER',
        write=True,
    )

    assert result["succeeded"] is True, result
    assert "nivel: '7'" in _read(tmp_path / "r1.md")


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


def test_null_on_an_explicit_null_keeps_it_and_a_drop_removes_it(tmp_path: Path) -> None:
    # The final state is the answer: `campo` was NULL and still is, so nothing
    # changed for it; dropping the column is how a field leaves every document.
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: GAB\ncampo:\n---\n# R1\n")

    kept = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = 'NOVO', campo = NULL WHERE setor = 'GAB'",
        write=True,
    )
    assert kept["succeeded"] is True, kept
    assert "campo:" in _read(tmp_path / "r1.md")

    dropped = apply_bundle(str(tmp_path), sql='ALTER TABLE "Rotina" DROP COLUMN campo', write=True)
    assert dropped["succeeded"] is True, dropped
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


def test_drop_then_add_same_column_name_compiles_even_when_unselected(tmp_path: Path) -> None:
    # DROP+ADD resets every row's "a" to NULL structurally, in the ALTER
    # phase - independent of whatever the trailing UPDATE's WHERE selects.
    # Gating compilation on `selected` alone would miss this: the row was
    # never selected, but its value genuinely changed (30 -> absent).
    _write(tmp_path / "r1.md", "---\ntype: Rotina\na: 30\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" DROP COLUMN a; '
            'ALTER TABLE "Rotina" ADD COLUMN a VARCHAR; '
            'UPDATE "Rotina" SET __okf_path = __okf_path WHERE FALSE'
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "a: 30" not in text
    assert "a:" not in text.split("---")[1]


def test_add_column_with_default_backfills_every_row_even_when_unselected(tmp_path: Path) -> None:
    # ADD COLUMN ... DEFAULT backfills every existing row structurally, not
    # just the ones the trailing UPDATE happens to select.
    _write(tmp_path / "r1.md", "---\ntype: Rotina\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            "ALTER TABLE \"Rotina\" ADD COLUMN novo VARCHAR DEFAULT 'x'; "
            'UPDATE "Rotina" SET __okf_path = __okf_path WHERE FALSE'
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "novo: x" in text or "novo: 'x'" in text


def test_rename_chain_is_scoped_to_its_own_type(tmp_path: Path) -> None:
    # TipoA's a->b->a cancels out to its exact original schema and rows - a
    # no-op the compiler should never see. TipoB has an unrelated column
    # also named "a", authored as an explicit null on both its rows; only
    # tb1 matches the trailing UPDATE's WHERE, so tb2's `a: null` should be
    # left untouched. If the rename chain were tracked globally instead of
    # per type, TipoA's cancelled a->a mapping would be mistaken for a
    # structural rename on TipoB, deleting `a` bundle-wide regardless of
    # selection - including on tb2, which the script never selected at all.
    _write(tmp_path / "ta.md", "---\ntype: TipoA\na: 1\n---\n# TA\n")
    _write(tmp_path / "tb1.md", "---\ntype: TipoB\na:\nx: 1\n---\n# TB1\n")
    _write(tmp_path / "tb2.md", "---\ntype: TipoB\na:\nx: 2\n---\n# TB2\n")

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "TipoA" RENAME COLUMN a TO b; '
            'ALTER TABLE "TipoA" RENAME COLUMN b TO a; '
            "UPDATE \"TipoB\" SET x = 'updated' WHERE x = '1'"
        ),
        write=True,
    )

    assert result["succeeded"] is True, result
    assert _changed_paths(result) == ["tb1.md"]
    assert "a: 1" in _read(tmp_path / "ta.md")
    tb2_frontmatter = _read(tmp_path / "tb2.md").split("---")[1]
    assert "a:" in tb2_frontmatter


def test_write_recheck_agrees_with_baseline_on_an_excluded_directory(tmp_path: Path) -> None:
    # An `.okfignore`-excluded directory must be pruned identically by the
    # baseline walk (`_snapshot_bundle`) and the write-time recheck
    # (`_snapshot_manifest`/`_build_candidate_tree`) - otherwise its files
    # appear on only one side of the final manifest comparison and every
    # valid write aborts with a false "bundle changed" conflict.
    _write(tmp_path / ".okfignore", "ignored/\n")
    _write(tmp_path / "ignored" / "x.md", "---\ntype: Rotina\nsetor: SKIP\n---\n# X\n")
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nsetor: GAB\n---\n# R1\n")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = '#GAB#FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "setor: '#GAB#FSB'" in text or "setor: #GAB#FSB" in text


def test_structured_field_collision_check_is_case_insensitive(tmp_path: Path) -> None:
    # "Tags" is structured (a list) on r2; DuckDB's identifier equality is
    # ASCII-case-insensitive, so `ADD COLUMN tags` collides with it exactly
    # as much as `ADD COLUMN Tags` would.
    _write(tmp_path / "r1.md", "---\ntype: Rotina\n---\n# R1\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\nTags:\n  - [a, b]\n---\n# R2\n")

    result = apply_bundle(
        str(tmp_path),
        sql=('ALTER TABLE "Rotina" ADD COLUMN tags VARCHAR; UPDATE "Rotina" SET tags = \'x\''),
        write=True,
    )

    assert result["succeeded"] is False
    assert "structured" in _error(result)
    assert "- [a, b]" in _read(tmp_path / "r2.md")


def _typed_spec(tmp_path: Path, ddl: str) -> str:
    spec = tmp_path / "docs" / "types"
    spec.mkdir(parents=True, exist_ok=True)
    _write(spec / "rotina.schema.sql", ddl)
    return "docs/types/{slug}.md"


def test_typed_apply_queries_decimal_without_rewriting_declared_value(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    _write(tmp_path / "r2.md", "---\ntype: Rotina\ncusto: 20.75\nstatus: low\n---\n")
    template = _typed_spec(
        tmp_path,
        'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n',
    )

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET status = 'high' WHERE custo > 15",
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    assert _changed_paths(result) == ["r2.md"]
    assert "custo: 10.50" in _read(tmp_path / "r1.md")
    assert "custo: 20.75" in _read(tmp_path / "r2.md")
    assert "status: high" in _read(tmp_path / "r2.md")


def test_typed_apply_divergent_value_is_null_and_still_queryable(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: n/a\nstatus: low\n---\n")
    template = _typed_spec(
        tmp_path,
        'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n',
    )

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET status = 'bad' WHERE custo IS NULL",
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "custo: n/a" in text
    assert "status: bad" in text


def test_typed_apply_rejects_direct_write_to_generated_declared_field(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET custo = 12.50 WHERE TRUE',
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "generated" in _error(result).lower()


def test_typed_apply_rejects_raw_carrier_tamper(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET __okf_raw_custo = '99.00' WHERE TRUE",
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "protected" in _error(result).lower()


def test_typed_apply_keeps_declared_fields_read_only(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')
    drop = 'ALTER TABLE "Rotina" DROP COLUMN custo'

    refused = apply_bundle(str(tmp_path), sql=drop, write=True, spec_template=template)
    assert refused["succeeded"] is False
    assert "declared field" in _error(refused)
    assert "custo:" in _read(tmp_path / "r1.md")

    # Without the template, a declared field is ordinary text.
    dropped = apply_bundle(str(tmp_path), sql=drop, write=True)
    assert dropped["succeeded"] is True, dropped
    assert "custo:" not in _read(tmp_path / "r1.md")


def test_typed_apply_rejects_rename_of_declared_field(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" RENAME COLUMN custo TO valor; '
            'UPDATE "Rotina" SET status = status WHERE FALSE'
        ),
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "declared field" in _error(result).lower()


def test_typed_apply_rejects_readding_dropped_declared_name(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" DROP COLUMN custo; '
            'ALTER TABLE "Rotina" ADD COLUMN custo VARCHAR; '
            'UPDATE "Rotina" SET status = status WHERE FALSE'
        ),
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "declared field" in _error(result).lower()


def test_typed_apply_queries_declared_list_without_serializing_it(tmp_path: Path) -> None:
    _write(
        tmp_path / "r1.md",
        "---\ntype: Rotina\ntags:\n- 1\n- 2\nstatus: low\n---\n",
    )
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (tags BIGINT[]);\n')

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET status = 'hit' WHERE list_contains(tags, 2)",
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    text = _read(tmp_path / "r1.md")
    assert "status: hit" in text
    assert "- 1" in text
    assert "- 2" in text


def test_typed_apply_declared_list_is_writable_and_enforces_constraints(tmp_path: Path) -> None:
    _write(
        tmp_path / "r1.md",
        "---\ntype: Rotina\ntags: [a]\n---\n# R1\n",
    )
    template = _typed_spec(
        tmp_path,
        'CREATE TABLE "Rotina" (tags VARCHAR[] NOT NULL CHECK (len(tags) > 0));\n',
    )

    updated = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET tags = list_append(tags, 'b')",
        write=True,
        spec_template=template,
    )
    assert updated["succeeded"] is True, updated
    assert "tags: [a, b]" in _read(tmp_path / "r1.md")

    null_result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET tags = NULL',
        write=True,
        spec_template=template,
    )
    assert null_result["succeeded"] is False
    assert "constraint" in _error(null_result).lower()

    empty_result = apply_bundle(
        str(tmp_path),
        sql='UPDATE "Rotina" SET tags = []',
        write=True,
        spec_template=template,
    )
    assert empty_result["succeeded"] is False
    assert "constraint" in _error(empty_result).lower()
    assert "tags: [a, b]" in _read(tmp_path / "r1.md")


def test_typed_apply_declared_unobserved_field_exists_as_null(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (futuro BIGINT);\n')

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET status = 'missing' WHERE futuro IS NULL",
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    assert "status: missing" in _read(tmp_path / "r1.md")
    assert "futuro:" not in _read(tmp_path / "r1.md")


def test_typed_apply_invalid_declaration_is_explicit_error(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\nstatus: low\n---\n")
    template = _typed_spec(tmp_path, "CREATE TABLE broken")

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET status = 'x' WHERE TRUE",
        spec_template=template,
    )

    assert result["succeeded"] is False
    assert "declared schema" in _error(result).lower()


def test_typed_apply_keeps_undeclared_alter_add_semantics(tmp_path: Path) -> None:
    _write(tmp_path / "r1.md", "---\ntype: Rotina\ncusto: 10.50\n---\n")
    template = _typed_spec(tmp_path, 'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n')

    result = apply_bundle(
        str(tmp_path),
        sql=(
            'ALTER TABLE "Rotina" ADD COLUMN status VARCHAR; '
            "UPDATE \"Rotina\" SET status = 'ok' WHERE custo > 5"
        ),
        write=True,
        spec_template=template,
    )

    assert result["succeeded"] is True, result
    assert "status: ok" in _read(tmp_path / "r1.md")


def test_frontmatter_the_old_round_trip_check_refused_is_now_edited_losslessly(
    tmp_path: Path,
) -> None:
    source = "---\n{type: Rotina, setor: GAB, note: 'kept # as is'}\n---\n# R1\n"
    _write(tmp_path / "r1.md", source)

    result = apply_bundle(
        str(tmp_path),
        sql="UPDATE \"Rotina\" SET setor = 'FSB' WHERE setor = 'GAB'",
        write=True,
    )

    assert result["succeeded"] is True, result
    assert _read(tmp_path / "r1.md") == source.replace("setor: GAB", 'setor: "FSB"')