"""Tests for the canonical Rust frontmatter bridge."""

from pathlib import Path
from unittest.mock import Mock

from okf_parser.rust_core import rust_render_frontmatter


def test_rust_render_frontmatter_sends_typed_json_and_returns_bytes(monkeypatch) -> None:
    completed = Mock(returncode=0, stdout=b"---\nitems:\n  - one\n---\n", stderr=b"")
    run = Mock(return_value=completed)
    monkeypatch.setattr("okf_parser.rust_core.subprocess.run", run)

    result = rust_render_frontmatter({"items": ["one"], "count": 3}, Path("/core"))

    assert result == completed.stdout
    command = run.call_args.args[0]
    assert command == [Path("/core"), "__engine-render-frontmatter"]
    payload = run.call_args.kwargs["input"]
    assert b'"items":["one"]' in payload
    assert b'"count":3' in payload
