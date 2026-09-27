"""Type packs: ordinary OKF specification files the binary installs.

``okf-parser packs`` and ``add-pack`` run in ``okf-engine/src/packs.rs``; the
packs shipped with okf-parser are embedded from ``okf-engine/packs``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from okf_parser.rust_core import packaged_rust_core
from okf_parser.service import init_bundle

_CONFIGURED = os.environ.get("OKF_CORE")
_BINARY = Path(_CONFIGURED) if _CONFIGURED else packaged_rust_core()
pytestmark = pytest.mark.skipif(_BINARY is None, reason="the native okf-parser binary is not built")
_JOURNALISM = Path(__file__).parents[1] / "okf-engine" / "packs" / "journalism"
_JOURNALISM_FILES = [
    "body-mapping.json",
    "ninjs-mapping.json",
    "specs/body.md",
    "specs/body.schema.sql",
    "specs/newsitem.md",
    "specs/newsitem.schema.sql",
    "standards/GeoJSON.json",
    "standards/ninjs-schema_3.2.json",
]


def _run(*args: str) -> tuple[int, Any]:
    assert _BINARY is not None
    completed = subprocess.run(  # noqa: S603 - fixed argv to the binary under test
        [str(_BINARY), *args], capture_output=True, check=False, encoding="utf-8"
    )
    if not completed.stdout:
        return completed.returncode, completed.stderr
    return completed.returncode, json.loads(completed.stdout)


def test_journalism_pack_is_embedded() -> None:
    code, payload = _run("packs")

    assert code == 0
    packs = {pack["name"]: pack for pack in payload["packs"]}
    journalism = packs["journalism"]
    assert journalism["types"] == ["Body", "NewsItem"]
    assert journalism["standard"] == "IPTC ninjs"
    assert journalism["standard_version"] == "3.2"
    assert journalism["files"] == _JOURNALISM_FILES


def test_journalism_contract_is_scaffolded_from_authored_examples(tmp_path: Path) -> None:
    """The committed starter schemas must be reproducible by the parser's own init command."""
    root = tmp_path / "journalism"
    shutil.copytree(_JOURNALISM / "examples", root / "examples")

    result = init_bundle(root.as_posix(), "specs/{slug}.md", write=True, infer_schema=True)
    specs = cast("dict[str, object]", result["specs"])
    schemas = cast("dict[str, object]", result["schemas"])

    assert specs["created"] == ["specs/body.md", "specs/newsitem.md"]
    assert schemas["created"] == ["specs/body.schema.sql", "specs/newsitem.schema.sql"]
    for name in ("body.schema.sql", "newsitem.schema.sql"):
        generated = (root / "specs" / name).read_text(encoding="utf-8")
        assert generated == (_JOURNALISM / "specs" / name).read_text(encoding="utf-8")


def test_add_pack_previews_writes_and_is_idempotent(tmp_path: Path) -> None:
    code, preview = _run("add-pack", "journalism", str(tmp_path))

    assert code == 0
    assert preview["written"] == []
    assert preview["collisions"] == []
    assert preview["planned"] == _JOURNALISM_FILES
    assert not (tmp_path / "specs/body.md").exists()

    code, committed = _run("add-pack", "journalism", str(tmp_path), "--write")
    assert code == 0
    assert committed["written"] == preview["planned"]
    for path in _JOURNALISM_FILES:
        assert (tmp_path / path).read_bytes() == (_JOURNALISM / path).read_bytes()

    code, repeated = _run("add-pack", "journalism", str(tmp_path), "--write")
    assert code == 0
    assert repeated["planned"] == []
    assert repeated["written"] == []
    assert repeated["unchanged"] == committed["written"]


def test_add_pack_exits_nonzero_and_writes_nothing_on_collision(tmp_path: Path) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "newsitem.md").write_text("different\n", encoding="utf-8")

    code, result = _run("add-pack", "journalism", str(tmp_path), "--write")

    assert code == 1
    assert result["collisions"] == ["specs/newsitem.md"]
    assert result["written"] == []
    assert not (specs / "body.schema.sql").exists()


def test_a_pack_directory_installs_the_files_its_manifest_lists(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    (pack / "specs").mkdir(parents=True)
    (pack / "pack.json").write_text(
        json.dumps(
            {
                "name": "local",
                "version": "1",
                "description": "A local pack.",
                "types": ["Note"],
                "files": ["specs"],
            }
        ),
        encoding="utf-8",
    )
    (pack / "specs" / "note.md").write_text(
        "---\ntype: ConceptSpecification\nconcept_type: Note\n---\n", encoding="utf-8"
    )
    (pack / "README.md").write_text("not installed\n", encoding="utf-8")
    bundle = tmp_path / "bundle"

    code, result = _run("add-pack", str(pack), str(bundle), "--write")

    assert code == 0
    assert result["pack"]["name"] == "local"
    assert result["written"] == ["specs/note.md"]
    assert not (bundle / "README.md").exists()


def test_an_unknown_pack_names_the_embedded_ones() -> None:
    code, stderr = _run("add-pack", "no-such-pack")

    assert code == 1
    assert "available packs: journalism" in stderr
