"""Tests for deterministic text writes."""

from pathlib import Path

from okf_parser.io_support import atomic_write_lf_text, lf_bytes


def test_lf_bytes_normalizes_every_newline_spelling() -> None:
    assert lf_bytes("a\r\nb\rc\n") == b"a\nb\nc\n"


def test_atomic_write_lf_text_commits_utf8_lf_bytes(tmp_path: Path) -> None:
    target = tmp_path / "nested.md"
    atomic_write_lf_text(target, "á\r\nβ\r")

    assert target.read_bytes() == "á\nβ\n".encode()
    assert not target.with_name(f".{target.name}.okf-write.tmp").exists()
