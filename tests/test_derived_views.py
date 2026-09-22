"""Tests for deterministic disposable index.md/log.md runtime projections."""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.derived_views import materialize_derived_views, render_index, render_log

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_index_is_derived_from_concepts_and_grouped_by_type(tmp_path: Path) -> None:
    _write(tmp_path / "b.md", "---\ntype: Note\ntitle: Beta\n---\n")
    _write(tmp_path / "a.md", "---\ntype: Decision\ntitle: Alpha\n---\n")

    rendered = render_index(tmp_path)

    assert "GENERATED FILE — DO NOT EDIT" in rendered
    assert rendered.index("## Decision") < rendered.index("## Note")
    assert "- [Alpha](a.md)" in rendered
    assert "- [Beta](b.md)" in rendered


def test_log_uses_only_explicit_iso_frontmatter_dates(tmp_path: Path) -> None:
    _write(tmp_path / "new.md", "---\ntype: Note\ntitle: New\nupdated: '2026-09-22'\n---\n")
    _write(tmp_path / "old.md", "---\ntype: Note\ntitle: Old\ncreated: '2026-01-01'\n---\n")
    _write(tmp_path / "undated.md", "---\ntype: Note\ntitle: Undated\n---\n")

    rendered = render_log(tmp_path)

    assert rendered.index("## 2026-09-22") < rendered.index("## 2026-01-01")
    assert "New" in rendered
    assert "Old" in rendered
    assert "Undated" not in rendered


def test_materialize_writes_views_and_opinionated_gitignore(tmp_path: Path) -> None:
    _write(tmp_path / "concept.md", "---\ntype: Note\ntitle: Concept\n---\n")

    payload = materialize_derived_views(tmp_path, write=True)

    assert payload["written"] == ["index.md", "log.md"]
    assert "generated" not in payload
    views = payload["views"]
    assert isinstance(views, dict)
    index_view = views["index.md"]
    assert isinstance(index_view, dict)
    assert index_view["bytes"] > 0
    assert len(index_view["sha256"]) == 64
    assert (tmp_path / "index.md").read_text(encoding="utf-8").startswith("<!-- GENERATED FILE")
    assert (tmp_path / "log.md").exists()
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()[-2:] == [
        "/index.md",
        "/log.md",
    ]


def test_preview_does_not_touch_filesystem(tmp_path: Path) -> None:
    _write(tmp_path / "concept.md", "---\ntype: Note\n---\n")

    payload = materialize_derived_views(tmp_path)

    assert payload["written"] == []
    assert "generated" in payload
    assert not (tmp_path / "index.md").exists()
    assert not (tmp_path / "log.md").exists()
    assert not (tmp_path / ".gitignore").exists()


def test_materialize_is_idempotent(tmp_path: Path) -> None:
    _write(tmp_path / "concept.md", "---\ntype: Note\ntitle: Concept\n---\n")

    first = materialize_derived_views(tmp_path, write=True)
    first_index = (tmp_path / "index.md").read_bytes()
    first_log = (tmp_path / "log.md").read_bytes()
    first_gitignore = (tmp_path / ".gitignore").read_bytes()

    second = materialize_derived_views(tmp_path, write=True)

    assert (tmp_path / "index.md").read_bytes() == first_index
    assert (tmp_path / "log.md").read_bytes() == first_log
    assert (tmp_path / ".gitignore").read_bytes() == first_gitignore
    assert first["views"] == second["views"]
    assert first["gitignore_changed"] is True
    assert second["gitignore_changed"] is False


def test_generated_links_percent_encode_markdown_sensitive_path_characters(tmp_path: Path) -> None:
    _write(
        tmp_path / "odd )#?.md",
        "---\ntype: Note\ntitle: Adversarial path\nupdated: '2026-09-22'\n---\n",
    )

    index = render_index(tmp_path)
    log = render_log(tmp_path)

    target = "odd%20%29%23%3F.md"
    assert f"[Adversarial path]({target})" in index
    assert f"[Adversarial path]({target})" in log
