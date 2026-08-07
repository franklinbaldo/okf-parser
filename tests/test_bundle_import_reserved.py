"""Regression tests for reserved identity during bundle import."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.bundle_import import BundleImportError, import_bundle

if TYPE_CHECKING:
    from pathlib import Path


def test_source_type_column_cannot_override_cli_type(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("id,type,nome\nr1,Empresa,Ana\n", encoding="utf-8")

    with pytest.raises(BundleImportError, match="source column 'type' is reserved"):
        import_bundle(
            str(source),
            str(tmp_path / "bundle"),
            "Pessoa",
            id_column="id",
            write=True,
        )

    assert not (tmp_path / "bundle").exists()
