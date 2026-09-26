"""``Bundle.sql()``: one read-only query over a loaded bundle."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from okf_parser import SqlError, load_bundle
from okf_parser.declared_schema import DeclaredSchemaError
from okf_parser.sql import decode_value

if TYPE_CHECKING:
    from pathlib import Path


def _declared_bundle(root: Path) -> str:
    (root / "a.md").write_text(
        "---\ntype: Rotina\ncusto: 12.50\ncodigo: alpha\n---\nA\n[B](b.md)\n",
        encoding="utf-8",
    )
    (root / "b.md").write_text(
        "---\ntype: Rotina\ncusto: n/a\ncodigo: beta\n---\nB\n",
        encoding="utf-8",
    )
    types = root / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        'CREATE TABLE "Rotina" (custo DECIMAL(18,2));\n',
        encoding="utf-8",
    )
    return "docs/types/{slug}.md"


def test_the_bundle_tables_answer_sql(tmp_path: Path) -> None:
    _declared_bundle(tmp_path)
    bundle = load_bundle(tmp_path)

    result = bundle.sql(
        "SELECT c.concept_id, t.concept_id AS target FROM links l "
        "JOIN concepts c ON c.concept_id = l.source_id "
        "JOIN concepts t ON t.concept_id = l.target_id"
    )

    assert result.column_names == ("concept_id", "target")
    assert result.rows == (("a", "b"),)
    assert result.to_dicts() == [{"concept_id": "a", "target": "b"}]
    assert not result.truncated


def test_declared_types_are_typed_tables(tmp_path: Path) -> None:
    template = _declared_bundle(tmp_path)

    result = load_bundle(tmp_path).sql(
        'SELECT "__okf_path", custo, codigo FROM Rotina ORDER BY 1', spec_template=template
    )

    assert [column.type for column in result.columns] == ["VARCHAR", "DECIMAL(18,2)", "VARCHAR"]
    assert result.rows == (("a.md", Decimal("12.50"), "alpha"), ("b.md", None, "beta"))


def test_the_query_sees_the_snapshot_not_the_disk(tmp_path: Path) -> None:
    _declared_bundle(tmp_path)
    bundle = load_bundle(tmp_path)
    (tmp_path / "c.md").write_text("---\ntype: Rotina\n---\n", encoding="utf-8")

    assert bundle.sql("SELECT count(*) FROM concepts").rows == ((2,),)


def test_values_come_back_as_python_values(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Note\n---\n", encoding="utf-8")

    result = load_bundle(tmp_path).sql(
        "SELECT DATE '2026-01-15' AS day, TIMESTAMPTZ '2026-01-15 09:30:00+00' AS at, "
        "TIME '09:30:00' AS t, '\\x01'::BLOB AS b, "
        "170141183460469231731687303715884105727::HUGEINT AS h, "
        "[DATE '2026-01-15'] AS days, {'n': 1.5::DECIMAL(4,1)} AS s, "
        "MAP {'k': 2} AS m, '{\"a\": [1]}'::JSON AS j, uuid() AS u"
    )

    row = result.to_dicts()[0]
    assert row["day"] == date(2026, 1, 15)
    assert row["at"] == datetime(2026, 1, 15, 9, 30, tzinfo=UTC)
    assert row["t"] == time(9, 30)
    assert row["b"] == b"\x01"
    assert row["h"] == 2**127 - 1
    assert row["days"] == [date(2026, 1, 15)]
    assert row["s"] == {"n": Decimal("1.5")}
    assert row["m"] == {"k": 2}
    assert row["j"] == {"a": [1]}
    assert isinstance(row["u"], uuid.UUID)


def test_a_limit_truncates(tmp_path: Path) -> None:
    _declared_bundle(tmp_path)

    result = load_bundle(tmp_path).sql("FROM concepts", limit=1)

    assert len(result) == 1
    assert result.truncated


@pytest.mark.parametrize(
    "query",
    [
        "SELEC 1",
        "CREATE TABLE t (x INTEGER)",
        "SELECT * FROM read_csv('/etc/hosts')",
        "COPY (SELECT 1) TO 'escape.csv'",
    ],
)
def test_only_one_read_only_query_over_the_bundle_runs(tmp_path: Path, query: str) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Note\n---\n", encoding="utf-8")

    with pytest.raises(SqlError):
        load_bundle(tmp_path).sql(query)


def test_declared_schema_failures_surface(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Node\n---\n", encoding="utf-8")
    types = tmp_path / "docs" / "types"
    types.mkdir(parents=True)
    (types / "node.schema.sql").write_text(
        'CREATE TABLE "Other" (value BIGINT);\n', encoding="utf-8"
    )

    with pytest.raises(DeclaredSchemaError):
        load_bundle(tmp_path).sql("SELECT 1", spec_template="docs/types/{slug}.md")


def test_decoding_follows_nested_types() -> None:
    assert decode_value([["a", [1]]], "MAP(VARCHAR, INTEGER[])") == {"a": [1]}
    assert decode_value({"a b": "1.0"}, 'STRUCT("a b" DECIMAL(2,1))') == {"a b": Decimal("1.0")}
    assert decode_value("infinity", "DATE") == "infinity"
    assert decode_value([[[1], 2]], "MAP(INTEGER[], INTEGER)") == [([1], 2)]
    assert decode_value("NaN", "DOUBLE") != decode_value("NaN", "DOUBLE")
