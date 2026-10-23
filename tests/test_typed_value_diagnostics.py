"""A declared value its ``.schema.sql`` type cannot hold is reported (OKF023).

Regression tests for #319 (a value outside an ENUM, an invalid DATE) and
#316 (a list of mappings or a mapping under a STRUCT[]/MAP column): each used
to become NULL in the typed table with no diagnostic while ``check`` stayed
``conformant: true``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import duckdb

from okf_parser import load_bundle
from okf_parser.service import check_bundle, export_duckdb

if TYPE_CHECKING:
    from pathlib import Path

TEMPLATE = ".okf/specs/{slug}.md"


def _bundle(root: Path, frontmatter: str) -> None:
    specs = root / ".okf" / "specs"
    specs.mkdir(parents=True)
    (specs / "memoria.schema.sql").write_text(
        "CREATE TYPE memoria_tipo AS ENUM ('usuario', 'feedback');\n"
        'CREATE TABLE "Memoria" (title VARCHAR, tipo memoria_tipo, registrada_em DATE, '
        "n BIGINT, tags VARCHAR[], ha_args MAP(VARCHAR, VARCHAR), "
        "commands STRUCT(key VARCHAR, code VARCHAR)[]);\n",
        encoding="utf-8",
    )
    for slug, title in (("memoria", "Memoria"), ("spec", "Spec")):
        (specs / f"{slug}.md").write_text(
            f"---\ntype: Spec\ntitle: {title}\n---\n# {title}\n", encoding="utf-8"
        )
    (root / "ok.md").write_text(
        "---\ntype: Memoria\ntitle: Ok\ntipo: usuario\nregistrada_em: 2026-10-22\n"
        "n: 5\ntags: [a, b]\n---\n# Ok\n",
        encoding="utf-8",
    )
    (root / "bad.md").write_text(f"---\ntype: Memoria\n{frontmatter}---\n# Bad\n", encoding="utf-8")


def _typed(report: dict[str, object]) -> list[dict[str, str]]:
    diagnostics = cast("list[dict[str, str]]", report["diagnostics"])
    return [item for item in diagnostics if item["code"] == "OKF023"]


def test_check_reports_a_value_outside_the_enum_319(tmp_path: Path) -> None:
    _bundle(tmp_path, "tipo: errado\nregistrada_em: 2026-13-40\n")

    report = check_bundle(str(tmp_path), require_spec=TEMPLATE, normative_spec=True)

    typed = _typed(report)
    assert report["conformant"] is False
    assert {item["path"] for item in typed} == {"bad.md"}
    assert {item["severity"] for item in typed} == {"error"}
    messages = " ".join(item["message"] for item in typed)
    assert "`tipo` value 'errado'" in messages
    assert "`registrada_em` value '2026-13-40'" in messages


def test_check_reports_structured_values_316(tmp_path: Path) -> None:
    _bundle(
        tmp_path,
        "ha_args:\n  ligar: b64:x\n  desligar: b64:y\ncommands:\n  - key: ligar\n    code: b64:x\n",
    )

    report = check_bundle(str(tmp_path), require_spec=TEMPLATE, normative_spec=True)

    typed = _typed(report)
    assert report["conformant"] is False
    assert sorted(item["message"].split("`")[3] for item in typed) == ["commands", "ha_args"]
    assert all(item["path"] == "bad.md" for item in typed)


def test_without_normative_spec_the_finding_is_a_warning(tmp_path: Path) -> None:
    _bundle(tmp_path, "tipo: errado\n")

    report = check_bundle(str(tmp_path), require_spec=TEMPLATE)

    assert [item["severity"] for item in _typed(report)] == ["warning"]
    assert report["conformant"] is True


def test_valid_values_and_absent_keys_are_not_reported(tmp_path: Path) -> None:
    _bundle(tmp_path, "title: Bad only by name\n")

    report = check_bundle(str(tmp_path), require_spec=TEMPLATE, normative_spec=True)

    assert _typed(report) == []
    assert report["conformant"] is True


def test_sql_diagnostics_carry_the_failure_319(tmp_path: Path) -> None:
    _bundle(tmp_path, "tipo: errado\n")

    result = load_bundle(tmp_path).sql(
        "SELECT code, severity, path FROM okf.diagnostics WHERE code = 'OKF023'",
        spec_template=TEMPLATE,
    )

    assert [list(row) for row in result.rows] == [["OKF023", "error", "bad.md"]]


def test_duckdb_export_reports_the_failure(tmp_path: Path) -> None:
    _bundle(tmp_path, "ha_args:\n  ligar: b64:x\n")
    database = tmp_path / "knowledge.duckdb"

    result = dict(export_duckdb(str(tmp_path), str(database), "okf", spec_template=TEMPLATE))

    assert result["conformant"] is False
    with duckdb.connect(database, read_only=True) as connection:
        rows = connection.execute(
            "SELECT path FROM okf.diagnostics WHERE code = 'OKF023'"
        ).fetchall()
    assert rows == [("bad.md",)]
