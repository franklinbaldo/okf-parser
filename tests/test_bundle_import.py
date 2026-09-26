"""Tests for importing a DuckDB-readable source into an OKF bundle."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from okf_parser.bundle_import import BundleImportError, import_bundle
from okf_parser.parser import parse_document


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
    """A crash mid-import must not leave a half-written concept in the bundle.

    apply/edit go through write_support.write_raw, which stages to a temp file
    and renames; import_bundle wrote destinations directly, so a crash during
    one write_text left a truncated concept behind (silent-risk note in #171).
    Every document that reaches disk must be byte-identical to a clean write.
    """
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    documents = bundle / "pessoa"

    real_write_text = Path.write_text
    writes = {"count": 0}
    crash_message = "simulated crash mid-write"

    def flaky_write_text(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if self.parent == documents:
            writes["count"] += 1
            if writes["count"] == 2:
                real_write_text(
                    self,
                    data[: len(data) // 2],
                    encoding=encoding,
                    errors=errors,
                    newline=newline,
                )
                raise _SimulatedCrashError(crash_message)
        return real_write_text(
            self,
            data,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(_SimulatedCrashError, match="simulated crash mid-write"):
        import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    clean = tmp_path / "clean"
    monkeypatch.undo()
    import_bundle(str(csv), str(clean), "Pessoa", id_column="id", write=True)
    for path in documents.glob("*.md"):
        assert path.read_bytes() == (clean / "pessoa" / path.name).read_bytes(), (
            f"{path.name} was left truncated by the failed import"
        )


def test_write_always_uses_lf_line_endings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r"""`import` must not let `Path.write_text` translate `\n` to `os.linesep` (#259)."""
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    recorded: list[str | None] = []
    real_write_text = Path.write_text

    def recording_write_text(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        recorded.append(newline)
        return real_write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", recording_write_text)

    import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    assert recorded == ["\n", "\n"]
    assert b"\r\n" not in (bundle / "pessoa" / "r1.md").read_bytes()
    assert b"\r\n" not in (bundle / "pessoa" / "r2.md").read_bytes()


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


def test_list_column_is_written_as_a_yaml_sequence(tmp_path: Path) -> None:
    """A LIST source column round-trips as a real YAML list, not `str(value)` (#260)."""
    source = tmp_path / "source.parquet"
    pq.write_table(
        pa.table(
            {
                "id": ["c-1"],
                "pastas": pa.array([["u1", "u2"]], pa.list_(pa.string())),
            }
        ),
        source,
    )
    bundle = tmp_path / "bundle"

    result = import_bundle(str(source), str(bundle), "Tipo", id_column="id", write=True)

    assert result["created"] == ["tipo/c-1.md"]
    destination = bundle / "tipo" / "c-1.md"
    content = destination.read_text(encoding="utf-8")
    assert "pastas:\n- u1\n- u2\n" in content

    parsed = parse_document(destination)
    assert parsed.frontmatter["pastas"] == ["u1", "u2"]


def test_struct_column_is_written_as_a_yaml_mapping(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    pq.write_table(
        pa.table(
            {
                "id": ["c-1"],
                "endereco": pa.array(
                    [{"cidade": "Porto Velho", "numero": 42}],
                    pa.struct([("cidade", pa.string()), ("numero", pa.int64())]),
                ),
            }
        ),
        source,
    )
    bundle = tmp_path / "bundle"

    import_bundle(str(source), str(bundle), "Tipo", id_column="id", write=True)

    destination = bundle / "tipo" / "c-1.md"
    content = destination.read_text(encoding="utf-8")
    assert "cidade: Porto Velho" in content
    assert "numero: 42" in content
    # okf_parser's own reader normalizes every scalar leaf to its spelling
    # (see parser._StringScalarLoader), so the structure round-trips but the
    # nested number comes back as the string it's spelled as on disk.
    parsed = parse_document(destination)
    assert parsed.frontmatter["endereco"] == {"cidade": "Porto Velho", "numero": "42"}


def test_numbers_and_booleans_are_written_as_native_yaml_scalars(tmp_path: Path) -> None:
    """Integers/booleans are spelled unquoted instead of as quoted text (#260)."""
    source = tmp_path / "source.parquet"
    pq.write_table(
        pa.table({"id": ["c-1"], "caixa_id": [915], "ativo": [True]}),
        source,
    )
    bundle = tmp_path / "bundle"

    import_bundle(str(source), str(bundle), "Tipo", id_column="id", write=True)

    destination = bundle / "tipo" / "c-1.md"
    content = destination.read_text(encoding="utf-8")
    assert "caixa_id: 915" in content
    assert "ativo: true" in content
    assert "'915'" not in content
    assert "'true'" not in content
