"""Tests for reusable type packs authored as ordinary OKF bundles."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from okf_parser.service import init_bundle
from okf_parser.type_packs import install_type_pack, journalism_pack, list_type_packs

if TYPE_CHECKING:
    from pathlib import Path


def _copy_resource_tree(resource_path: str, destination: Path) -> None:
    """Copy packaged UTF-8 fixture resources without assuming a filesystem-backed wheel."""
    source = files("okf_parser").joinpath(*resource_path.split("/"))
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_resource_tree(f"{resource_path}/{child.name}", target)
        elif child.is_file():
            target.write_text(child.read_text(encoding="utf-8"), encoding="utf-8")


def test_journalism_pack_is_registered_from_pyproject_metadata() -> None:
    packs = {str(pack["name"]): pack for pack in list_type_packs()}

    journalism = packs["journalism"]
    assert journalism["types"] == ["NewsItem"]
    assert journalism["standard"] == "IPTC ninjs"
    assert journalism["standard_version"] == "3.2"
    assert journalism["files"] == ["newsitem.md", "newsitem.schema.sql"]


def test_journalism_contract_is_scaffolded_from_authored_examples(tmp_path: Path) -> None:
    """The committed starter schema must be reproducible by the parser's own init command."""
    root = tmp_path / "journalism"
    examples = root / "examples"
    examples.mkdir(parents=True)
    _copy_resource_tree("packs/journalism/examples", examples)

    result = init_bundle(
        root,
        "specs/{slug}.md",
        write=True,
        infer_schema=True,
    )

    assert result["specs"]["created"] == ["specs/newsitem.md"]
    assert result["schemas"]["created"] == ["specs/newsitem.schema.sql"]
    generated = (root / "specs/newsitem.schema.sql").read_text(encoding="utf-8")
    expected = next(
        item.content for item in journalism_pack().files if item.path == "newsitem.schema.sql"
    )
    assert generated == expected


def test_install_type_pack_previews_writes_and_is_idempotent(tmp_path: Path) -> None:
    preview = install_type_pack("journalism", tmp_path)

    assert preview["written"] == []
    assert preview["collisions"] == []
    assert preview["planned"] == ["newsitem.md", "newsitem.schema.sql"]
    assert not (tmp_path / "newsitem.md").exists()

    committed = install_type_pack("journalism", tmp_path, write=True)
    assert committed["written"] == preview["planned"]
    assert (tmp_path / "newsitem.md").is_file()
    assert (tmp_path / "newsitem.schema.sql").is_file()

    repeated = install_type_pack("journalism", tmp_path, write=True)
    assert repeated["planned"] == []
    assert repeated["written"] == []
    assert repeated["collisions"] == []
    assert repeated["unchanged"] == committed["written"]


def test_install_type_pack_aborts_batch_on_collision(tmp_path: Path) -> None:
    (tmp_path / "newsitem.md").write_text("different\n", encoding="utf-8")

    result = install_type_pack("journalism", tmp_path, write=True)

    assert result["collisions"] == ["newsitem.md"]
    assert result["written"] == []
    assert not (tmp_path / "newsitem.schema.sql").exists()
