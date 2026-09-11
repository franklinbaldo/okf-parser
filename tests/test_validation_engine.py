from __future__ import annotations

from pathlib import Path

import okf_parser.engine as engine_module
from okf_parser import validate_path


def test_public_validation_uses_auto_engine_selection(
    tmp_path: Path, monkeypatch
) -> None:
    seen: list[tuple[str, Path | None]] = []

    def resolve(*, engine: str, explicit: Path | None) -> None:
        seen.append((engine, explicit))
        return None

    monkeypatch.setattr(engine_module, "resolve_rust_core", resolve)
    (tmp_path / "concept.md").write_text(
        "---\ntype: Note\ntitle: Example\n---\n\n# Example\n",
        encoding="utf-8",
    )

    report = validate_path(tmp_path)

    assert report.is_conformant
    assert report.markdown_count == 1
    assert seen == [("auto", None)]


def test_public_validation_can_force_language_native_engine(
    tmp_path: Path, monkeypatch
) -> None:
    seen: list[tuple[str, Path | None]] = []

    def resolve(*, engine: str, explicit: Path | None) -> None:
        seen.append((engine, explicit))
        return None

    monkeypatch.setattr(engine_module, "resolve_rust_core", resolve)
    (tmp_path / "concept.md").write_text(
        "---\ntype: Note\ntitle: Example\n---\n\n# Example\n",
        encoding="utf-8",
    )

    report = validate_path(tmp_path, engine="native")

    assert report.is_conformant
    assert seen == [("native", None)]
