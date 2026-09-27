"""Tests for command exit codes, which automation depends on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest

from okf_parser.cli import app
from okf_parser.service import schema_bundle

if TYPE_CHECKING:
    from pathlib import Path


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
