"""Tests for command exit codes, which automation depends on."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

import pytest

from okf_parser.cli import app, format_command
from okf_parser.service import schema_bundle

if TYPE_CHECKING:
    from pathlib import Path


def test_check_cli_can_explain_bundle_classification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "concept.md").write_text("---\ntype: Note\n---\n", encoding="utf-8")

    app(["check", str(tmp_path), "--classify"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["classification"] == {
        "concepts": ["concept.md"],
        "reserved": [],
        "ignored": [],
        "invalid_or_untyped": [],
    }


def test_format_write_exits_nonzero_when_a_file_was_skipped(tmp_path: Path) -> None:
    (tmp_path / "unreadable.md").write_bytes(b"# Caf\xe9\n")

    result = format_command(str(tmp_path), write=True)

    assert result.exit_code == 1
    assert result.payload["skipped_paths"] == ["unreadable.md"]


def test_format_write_exits_zero_once_every_file_is_formatted(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Heading\n\n-   item\n", encoding="utf-8")

    result = format_command(str(tmp_path), write=True)

    assert result.exit_code == 0
    assert result.payload["changed_paths"] == ["a.md"]


def test_format_check_exits_nonzero_when_a_file_needs_formatting(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# Heading\n\n-   item\n", encoding="utf-8")

    assert format_command(str(tmp_path)).exit_code == 1


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
