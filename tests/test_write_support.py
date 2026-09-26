"""Tests for the Python write primitives that still serve ``apply``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.write_support import LOCK_FILE, snapshot_bundle, snapshot_manifest

if TYPE_CHECKING:
    from pathlib import Path


def test_the_native_commit_lock_is_not_part_of_the_bundle(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Note\n---\n# A\n", encoding="utf-8")
    before = snapshot_manifest(tmp_path, ())
    (tmp_path / LOCK_FILE).touch()
    assert snapshot_manifest(tmp_path, ()) == before
    assert snapshot_bundle(tmp_path, ()).manifest == before
