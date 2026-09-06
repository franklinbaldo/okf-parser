"""Tests for opt-in reusable type packs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.cli import add_pack, packs
from okf_parser.type_packs import install_type_pack, journalism_pack, list_type_packs

if TYPE_CHECKING:
    from pathlib import Path


def test_journalism_pack_is_registered_from_package_metadata() -> None:
    registered = {pack["name"]: pack for pack in list_type_packs()}

    assert registered["journalism"]["standard"] == "IPTC ninjs"
    assert registered["journalism"]["standard_version"] == "3.2"
    assert registered["journalism"]["standard_schema"].endswith("ninjs-schema_3.2.json")


def test_journalism_pack_keeps_ninjs_source_semantics_explicit() -> None:
    pack = journalism_pack()
    spec = next(item.content for item in pack.files if item.path.endswith("news-item.md"))

    assert "ninjs `type` is authored as\n  `ninjs_type`" in spec
    assert "Do not use `infoSources` as a generic list of documentary evidence" in spec
    assert "auditable observation/retrieval history" in spec


def test_install_type_pack_previews_writes_and_is_idempotent(tmp_path: Path) -> None:
    preview = install_type_pack("journalism", tmp_path)

    assert preview["written"] == []
    assert preview["collisions"] == []
    assert preview["planned"] == [
        "specs/news-item.md",
        "specs/news-item.schema.sql",
    ]
    assert not (tmp_path / "specs").exists()

    committed = install_type_pack("journalism", tmp_path, write=True)
    assert committed["written"] == preview["planned"]
    assert (tmp_path / "specs" / "news-item.md").is_file()
    assert (tmp_path / "specs" / "news-item.schema.sql").is_file()

    repeated = install_type_pack("journalism", tmp_path, write=True)
    assert repeated["planned"] == []
    assert repeated["written"] == []
    assert repeated["collisions"] == []
    assert repeated["unchanged"] == committed["written"]


def test_install_type_pack_aborts_batch_on_collision(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "news-item.md").write_text("different\n", encoding="utf-8")

    result = install_type_pack("journalism", tmp_path, write=True)

    assert result["collisions"] == ["specs/news-item.md"]
    assert result["written"] == []
    assert not (specs / "news-item.schema.sql").exists()


def test_packs_cli_lists_registered_package_metadata() -> None:
    result = packs()
    registered = {pack["name"]: pack for pack in result.payload["packs"]}

    assert result.exit_code == 0
    assert registered["journalism"]["standard_version"] == "3.2"


def test_add_pack_cli_is_preview_first(tmp_path: Path) -> None:
    result = add_pack("journalism", str(tmp_path))

    assert result.exit_code == 0
    assert result.payload["planned"] == [
        "specs/news-item.md",
        "specs/news-item.schema.sql",
    ]
    assert not (tmp_path / "specs").exists()


def test_add_pack_cli_reports_collision_without_partial_write(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "news-item.md").write_text("authored\n", encoding="utf-8")

    result = add_pack("journalism", str(tmp_path), write=True)

    assert result.exit_code == 1
    assert result.payload["collisions"] == ["specs/news-item.md"]
    assert not (specs / "news-item.schema.sql").exists()
