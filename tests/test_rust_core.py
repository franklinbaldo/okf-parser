"""Cross-language contract tests for the optional end-to-end Rust engine."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pytest

from okf_parser.bundle import load_bundle

if TYPE_CHECKING:
    from ibis.expr.types import Table

_EXECUTABLE = os.environ.get("OKF_CORE")
pytestmark = pytest.mark.skipif(_EXECUTABLE is None, reason="OKF_CORE is not set")


def _rows(table: Table) -> list[dict[str, object]]:
    return table.execute().to_dict(orient="records")  # type: ignore[no-any-return]


def test_native_engine_matches_python_and_digest_vectors(tmp_path: Path) -> None:
    vectors = json.loads(Path("conformance/content-digests.json").read_text())["cases"]
    for index, case in enumerate(vectors):
        (tmp_path / f"case-{index}.md").write_text(case["source"], newline="")
    (tmp_path / "target.md").write_text("---\ntype: Note\n---\n# Target\n")
    (tmp_path / "links.md").write_text(
        "---\ntype: Note\ntitle: Links\n---\n# Links\n\n[target](target.md)\n"
    )
    (tmp_path / "index.md").write_text("# Bundle\n")

    python = load_bundle(tmp_path)
    native = load_bundle(tmp_path, rust_core=Path(_EXECUTABLE or ""))

    assert _rows(native.concepts) == _rows(python.concepts)
    assert _rows(native.reserved) == _rows(python.reserved)
    assert _rows(native.links) == _rows(python.links)
    assert native.validate() == python.validate()
    by_path = {row["path"]: row for row in _rows(native.concepts)}
    for index, case in enumerate(vectors):
        assert by_path[f"case-{index}.md"]["source_digest"] == case["source_digest"]
        assert by_path[f"case-{index}.md"]["parsed_digest"] == case["parsed_digest"]


def test_native_engine_materializes_duckdb(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "note.md").write_text("---\ntype: Note\n---\n# Note\n")
    database = tmp_path / "bundle.duckdb"
    subprocess.run(  # noqa: S603
        [_EXECUTABLE or "", "duckdb", root, database],
        check=True,
        capture_output=True,
        text=True,
    )
    with duckdb.connect(database, read_only=True) as connection:
        assert connection.sql("SELECT count(*) FROM okf.concepts").fetchone() == (1,)
        assert connection.sql("SELECT concept_type FROM okf.concepts").fetchone() == ("Note",)
