"""Tests for command exit codes, which automation depends on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest

from okf_parser.cli import app, import_command
from okf_parser.service import schema_bundle

if TYPE_CHECKING:
    from pathlib import Path


def test_import_exits_nonzero_for_a_divergent_existing_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("id,name\nr1,Expected\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    destination = bundle / "example" / "r1.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("---\ntype: Example\nid: r1\nname: Different\n---\n", encoding="utf-8")

    result = import_command(
        str(source),
        str(bundle),
        type="Example",
        id_column="id",
        on_conflict="verify-identical",
    )

    assert result.exit_code == 1
    assert result.payload["conflicting_existing"] == ["example/r1.md"]


@pytest.mark.parametrize("schema_format", ["zod", "pydantic"])
def test_text_schema_cli_preserves_single_trailing_newline(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    schema_format: Literal["zod", "pydantic"],
) -> None:
    (tmp_path / "concept.md").write_text(
        "---\ntype: Example\nname: value\n---\nBody\n",
        encoding="utf-8",
    )
    expected = schema_bundle(str(tmp_path), schema_format)

    app(["schema", str(tmp_path), "--format", schema_format])

    assert capsys.readouterr().out == expected
