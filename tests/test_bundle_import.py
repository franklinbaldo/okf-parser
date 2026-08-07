"""Tests for importing a DuckDB-readable source into an OKF bundle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.bundle_import import BundleImportError, import_bundle

if TYPE_CHECKING:
    from pathlib import Path


def _write_csv(path: Path) -> None:
    path.write_text("id,nome,idade\nr1,Ana,30\nr2,Beto,25\n", encoding="utf-8")


def test_dry_run_reports_would_create_and_writes_nothing(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"

    result = import_bundle(str(csv), str(bundle), "Pessoa", id_column="id")

    assert result["written"] is False
    assert result["created"] == []
    assert result["would_create"] == ["pessoa/r1.md", "pessoa/r2.md"]
    assert not bundle.exists()


def test_write_creates_one_document_per_row(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"

    result = import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    assert result["written"] is True
    assert result["created"] == ["pessoa/r1.md", "pessoa/r2.md"]
    content = (bundle / "pessoa/r1.md").read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "type: Pessoa" in content
    assert "nome: Ana" in content
    assert "idade: '30'" in content


def test_without_id_column_uses_a_zero_padded_row_index(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"

    result = import_bundle(str(csv), str(bundle), "Pessoa", write=True)

    assert result["created"] == ["pessoa/000000.md", "pessoa/000001.md"]


def test_existing_destination_is_skipped_without_overwrite(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("---\ntype: Pessoa\nnome: Custom\n---\n", encoding="utf-8")

    result = import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    assert result["skipped_existing"] == ["pessoa/r1.md"]
    assert result["created"] == ["pessoa/r2.md"]
    assert "Custom" in existing.read_text(encoding="utf-8")


def test_overwrite_replaces_an_existing_destination(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("---\ntype: Pessoa\nnome: Custom\n---\n", encoding="utf-8")

    result = import_bundle(
        str(csv), str(bundle), "Pessoa", id_column="id", write=True, overwrite=True
    )

    assert result["created"] == ["pessoa/r1.md", "pessoa/r2.md"]
    assert "Ana" in existing.read_text(encoding="utf-8")


def test_duplicate_id_slugs_block_the_whole_call(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    csv.write_text("id,nome\nr1,Ana\nr1,Beto\n", encoding="utf-8")
    bundle = tmp_path / "bundle"

    result = import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    assert result["written"] is False
    assert result["created"] == []
    assert result["duplicate_ids"] == ["r1"]
    assert not bundle.exists()


def test_unknown_id_column_raises(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"

    with pytest.raises(BundleImportError, match="id column"):
        import_bundle(str(csv), str(bundle), "Pessoa", id_column="missing")


def test_unreadable_source_raises(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"

    with pytest.raises(BundleImportError, match="could not read"):
        import_bundle(str(tmp_path / "missing.csv"), str(bundle), "Pessoa")
