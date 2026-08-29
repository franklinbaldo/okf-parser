"""Import preview tokens bind commit to the exact reviewed source and destinations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.bundle_import import BundleImportError, import_bundle
from okf_parser.cli import mcp_import_preview, mcp_import_write

if TYPE_CHECKING:
    from pathlib import Path


def _write_csv(path: Path, *, second_name: str = "Beto") -> None:
    path.write_text(
        f"id,nome,idade\nr1,Ana,30\nr2,{second_name},25\n",
        encoding="utf-8",
    )


def _preview(csv: Path, bundle: Path, *, overwrite: bool = False) -> dict[str, object]:
    return import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        overwrite=overwrite,
    )


def test_unchanged_preview_token_allows_commit(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    bundle = tmp_path / "bundle"
    _write_csv(csv)

    preview = _preview(csv, bundle)
    token = str(preview["preview_token"])
    assert token == _preview(csv, bundle)["preview_token"]

    result = import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        write=True,
        expected_preview_token=token,
    )

    assert result["written"] is True
    assert result["preview_token"] == token
    assert result["created"] == ["pessoa/r1.md", "pessoa/r2.md"]


def test_source_change_after_preview_fails_before_any_write(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    bundle = tmp_path / "bundle"
    _write_csv(csv)
    token = str(_preview(csv, bundle)["preview_token"])

    _write_csv(csv, second_name="Carla")

    with pytest.raises(BundleImportError, match="preview is stale"):
        import_bundle(
            str(csv),
            str(bundle),
            "Pessoa",
            id_column="id",
            write=True,
            expected_preview_token=token,
        )

    assert not bundle.exists()


def test_destination_appearing_after_create_preview_fails_atomically(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    bundle = tmp_path / "bundle"
    _write_csv(csv)
    token = str(_preview(csv, bundle)["preview_token"])

    appeared = bundle / "pessoa" / "r1.md"
    appeared.parent.mkdir(parents=True)
    appeared.write_text("---\ntype: Pessoa\nnome: External\n---\n", encoding="utf-8")

    with pytest.raises(BundleImportError, match="preview is stale"):
        import_bundle(
            str(csv),
            str(bundle),
            "Pessoa",
            id_column="id",
            write=True,
            expected_preview_token=token,
        )

    assert "External" in appeared.read_text(encoding="utf-8")
    assert not (bundle / "pessoa" / "r2.md").exists()


def test_overwrite_destination_change_after_preview_fails_atomically(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    bundle = tmp_path / "bundle"
    _write_csv(csv)
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("---\ntype: Pessoa\nnome: Old\n---\n", encoding="utf-8")

    token = str(_preview(csv, bundle, overwrite=True)["preview_token"])
    existing.write_text("---\ntype: Pessoa\nnome: Newer\n---\n", encoding="utf-8")

    with pytest.raises(BundleImportError, match="preview is stale"):
        import_bundle(
            str(csv),
            str(bundle),
            "Pessoa",
            id_column="id",
            write=True,
            overwrite=True,
            expected_preview_token=token,
        )

    assert "Newer" in existing.read_text(encoding="utf-8")
    assert not (bundle / "pessoa" / "r2.md").exists()


def test_mcp_surfaces_carry_preview_token_without_interpreting_it(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    bundle = tmp_path / "bundle"
    _write_csv(csv)

    preview = mcp_import_preview(str(csv), str(bundle), "Pessoa", id_column="id")
    token = str(preview["preview_token"])
    result = mcp_import_write(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        expected_preview_token=token,
    )

    assert result["written"] is True
    assert result["preview_token"] == token
