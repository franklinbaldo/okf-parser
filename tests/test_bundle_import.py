"""Tests for importing a DuckDB-readable source into an OKF bundle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from okf_parser import load_bundle
from okf_parser.bundle_import import BundleImportError, import_bundle


def _write_csv(path: Path) -> None:
    path.write_text("id,nome,idade\nr1,Ana,30\nr2,Beto,25\n", encoding="utf-8")


class _SimulatedCrashError(OSError):
    """Marks the fault injected by the atomicity regression test."""


def test_dry_run_reports_would_create_and_writes_nothing(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"

    result = import_bundle(str(csv), str(bundle), "Pessoa", id_column="id")

    assert result["written"] is False
    assert result["created"] == []
    assert result["would_create"] == ["pessoa/r1.md", "pessoa/r2.md"]
    assert not bundle.exists()


def test_write_creates_one_document_per_row(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"

    result = import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    assert result["written"] is True
    assert result["created"] == ["pessoa/r1.md", "pessoa/r2.md"]
    content = (bundle / "pessoa/r1.md").read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "type: Pessoa" in content
    assert "nome: Ana" in content
    assert "idade: 30" in content


def test_structured_values_round_trip_and_bytes_are_lf(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "c-1",
                    "pastas": ["u1", "u2"],
                    "numero": 915,
                    "ativo": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    root = tmp_path / "bundle"

    result = import_bundle(str(source), str(root), "Observação", id_column="id", write=True)

    assert result["created"] == ["observacao/c-1.md"]
    document = root / "observacao" / "c-1.md"
    raw = document.read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode()
    assert "numero: 915" in text
    assert "ativo: true" in text
    assert "pastas:" in text
    assert "- u1" in text
    assert "- u2" in text

    bundle = load_bundle(root)
    record = bundle.concepts.execute().iloc[0]
    frontmatter = json.loads(record["frontmatter_json"])
    assert frontmatter["pastas"] == ["u1", "u2"]


def test_without_id_column_uses_a_zero_padded_row_index(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"

    result = import_bundle(str(csv), str(bundle), "Pessoa", write=True)

    assert result["created"] == ["pessoa/000000.md", "pessoa/000001.md"]


def test_existing_destination_is_skipped_without_overwrite(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("---\ntype: Pessoa\nnome: Custom\n---\n", encoding="utf-8")

    result = import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    assert result["skipped_existing"] == ["pessoa/r1.md"]
    assert result["created"] == ["pessoa/r2.md"]
    assert "Custom" in existing.read_text(encoding="utf-8")


def test_failed_write_never_leaves_a_truncated_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash mid-import must not leave a half-written concept in the bundle."""
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    documents = bundle / "pessoa"

    real_write_bytes = Path.write_bytes
    writes = {"count": 0}
    crash_message = "simulated crash mid-write"

    def flaky_write_bytes(self: Path, data: bytes) -> int:
        if self.parent == documents:
            writes["count"] += 1
            if writes["count"] == 2:
                real_write_bytes(self, data[: len(data) // 2])
                raise _SimulatedCrashError(crash_message)
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

    with pytest.raises(_SimulatedCrashError, match="simulated crash mid-write"):
        import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    clean = tmp_path / "clean"
    monkeypatch.undo()
    import_bundle(str(csv), str(clean), "Pessoa", id_column="id", write=True)
    for path in documents.glob("*.md"):
        assert path.read_bytes() == (clean / "pessoa" / path.name).read_bytes(), (
            f"{path.name} was left truncated by the failed import"
        )


def test_verify_identical_classifies_an_idempotent_reapplication(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    result = import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        write=True,
        on_conflict="verify-identical",
    )

    assert result["written"] is True
    assert result["created"] == []
    assert result["matched_existing"] == ["pessoa/r1.md", "pessoa/r2.md"]
    assert result["conflicting_existing"] == []


def test_verify_identical_uses_parsed_value_not_yaml_spelling(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "---\nidade: 30\nnome: Ana\nid: r1\ntype: Pessoa\n---\n",
        encoding="utf-8",
    )

    result = import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        on_conflict="verify-identical",
    )

    assert result["matched_existing"] == ["pessoa/r1.md"]
    assert result["conflicting_existing"] == []


def test_verify_identical_rejects_a_divergent_identity_atomically(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "---\ntype: Pessoa\nid: r1\nnome: Outra\nidade: '30'\n---\n",
        encoding="utf-8",
    )

    result = import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        write=True,
        on_conflict="verify-identical",
    )

    assert result["written"] is False
    assert result["created"] == []
    assert result["would_create"] == ["pessoa/r2.md"]
    assert result["conflicting_existing"] == ["pessoa/r1.md"]
    assert not (bundle / "pessoa" / "r2.md").exists()


@pytest.mark.parametrize(
    "existing_text",
    [
        "---\ntype: Pessoa\nid: r1\nnome: Ana\nidade: '30'\n---\nBody inesperado\n",
        "---\ntype: Pessoa\nid: r1\nnome: Ana\nidade: '30'\nextra: null\n---\n",
        "não é um conceito OKF\n",
    ],
)
def test_verify_identical_fails_closed_for_unexpected_documents(
    tmp_path: Path, existing_text: str
) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(existing_text, encoding="utf-8")

    result = import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        on_conflict="verify-identical",
    )

    assert result["conflicting_existing"] == ["pessoa/r1.md"]


def test_verify_identical_cannot_be_combined_with_overwrite(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)

    with pytest.raises(BundleImportError, match="mutually exclusive"):
        import_bundle(
            str(csv),
            str(tmp_path / "bundle"),
            "Pessoa",
            id_column="id",
            overwrite=True,
            on_conflict="verify-identical",
        )


def test_overwrite_replaces_an_existing_destination(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("---\ntype: Pessoa\nnome: Custom\n---\n", encoding="utf-8")

    result = import_bundle(
        str(csv), str(bundle), "Pessoa", id_column="id", write=True, overwrite=True
    )

    assert result["created"] == ["pessoa/r1.md", "pessoa/r2.md"]
    assert "Ana" in existing.read_text(encoding="utf-8")


def test_duplicate_id_slugs_block_the_whole_call(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    csv.write_text("id,nome\nr1,Ana\nr1,Beto\n", encoding="utf-8")
    bundle = tmp_path / "bundle"

    result = import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    assert result["written"] is False
    assert result["created"] == []
    assert result["duplicate_ids"] == ["r1"]
    assert not bundle.exists()


def test_unknown_id_column_raises(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"

    with pytest.raises(BundleImportError, match="id column"):
        import_bundle(str(csv), str(bundle), "Pessoa", id_column="missing")


def test_unreadable_source_raises(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"

    with pytest.raises(BundleImportError, match="could not read"):
        import_bundle(str(tmp_path / "missing.csv"), str(bundle), "Pessoa")
