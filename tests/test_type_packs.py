"""Tests for reusable type packs authored as ordinary OKF bundles."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING, cast

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


def _journalism_files() -> list[str]:
    return [
        "body-mapping.json",
        "ninjs-mapping.json",
        "specs/body.md",
        "specs/body.schema.sql",
        "specs/newsitem.md",
        "specs/newsitem.schema.sql",
        "standards/GeoJSON.json",
        "standards/ninjs-schema_3.2.json",
    ]


def test_journalism_pack_is_registered_from_pyproject_metadata() -> None:
    packs = {str(pack["name"]): pack for pack in list_type_packs()}

    journalism = packs["journalism"]
    assert journalism["types"] == ["Body", "NewsItem"]
    assert journalism["standard"] == "IPTC ninjs"
    assert journalism["standard_version"] == "3.2"
    assert journalism["files"] == _journalism_files()


def test_journalism_contract_is_scaffolded_from_authored_examples(tmp_path: Path) -> None:
    """The committed starter schemas must be reproducible by the parser's own init command."""
    root = tmp_path / "journalism"
    examples = root / "examples"
    examples.mkdir(parents=True)
    _copy_resource_tree("packs/journalism/examples", examples)

    result = init_bundle(
        root.as_posix(),
        "specs/{slug}.md",
        write=True,
        infer_schema=True,
    )
    specs = cast("dict[str, object]", result["specs"])
    schemas = cast("dict[str, object]", result["schemas"])

    assert specs["created"] == ["specs/body.md", "specs/newsitem.md"]
    assert schemas["created"] == ["specs/body.schema.sql", "specs/newsitem.schema.sql"]

    packaged = {item.path: item.content for item in journalism_pack().files}
    for name in ("body.schema.sql", "newsitem.schema.sql"):
        generated = (root / "specs" / name).read_text(encoding="utf-8")
        assert generated == packaged[f"specs/{name}"]


def test_install_type_pack_previews_writes_and_is_idempotent(tmp_path: Path) -> None:
    preview = install_type_pack("journalism", tmp_path)

    assert preview["written"] == []
    assert preview["collisions"] == []
    assert preview["planned"] == _journalism_files()
    assert not (tmp_path / "specs/body.md").exists()
    assert not (tmp_path / "specs/newsitem.md").exists()

    committed = install_type_pack("journalism", tmp_path, write=True)
    assert committed["written"] == preview["planned"]
    assert (tmp_path / "body-mapping.json").is_file()
    assert (tmp_path / "specs/body.md").is_file()
    assert (tmp_path / "specs/body.schema.sql").is_file()
    assert (tmp_path / "specs/newsitem.md").is_file()
    assert (tmp_path / "specs/newsitem.schema.sql").is_file()

    repeated = install_type_pack("journalism", tmp_path, write=True)
    assert repeated["planned"] == []
    assert repeated["written"] == []
    assert repeated["collisions"] == []
    assert repeated["unchanged"] == committed["written"]


def test_install_type_pack_aborts_batch_on_collision(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "newsitem.md").write_text("different\n", encoding="utf-8")

    result = install_type_pack("journalism", tmp_path, write=True)

    assert result["collisions"] == ["specs/newsitem.md"]
    assert result["written"] == []
    assert not (specs / "body.schema.sql").exists()
    assert not (specs / "newsitem.schema.sql").exists()
