"""Contract tests for a pinned native binary (``OKF_CORE``), e.g. a release build."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from okf_parser.bundle import load_bundle

_EXECUTABLE = os.environ.get("OKF_CORE")
pytestmark = pytest.mark.skipif(_EXECUTABLE is None, reason="OKF_CORE is not set")


def test_pinned_binary_matches_the_installed_one_and_digest_vectors(tmp_path: Path) -> None:
    vectors = json.loads(Path("conformance/content-digests.json").read_text())["cases"]
    for index, case in enumerate(vectors):
        (tmp_path / f"case-{index}.md").write_text(case["source"], newline="")
    (tmp_path / "target.md").write_text("---\ntype: Note\n---\n# Target\n")
    (tmp_path / "links.md").write_text(
        "---\ntype: Note\ntitle: Links\n---\n# Links\n\n[target](target.md)\n"
    )
    (tmp_path / "index.md").write_text("# Bundle\n")

    installed = load_bundle(tmp_path)
    pinned = load_bundle(tmp_path, rust_core=Path(_EXECUTABLE or ""))

    assert pinned.concepts == installed.concepts
    assert pinned.reserved == installed.reserved
    assert pinned.links == installed.links
    assert pinned.validate() == installed.validate()
    by_path = {concept.path: concept for concept in pinned.concepts}
    for index, case in enumerate(vectors):
        assert by_path[f"case-{index}.md"].source_digest == case["source_digest"]
        assert by_path[f"case-{index}.md"].parsed_digest == case["parsed_digest"]
