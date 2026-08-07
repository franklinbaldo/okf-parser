from pathlib import Path

path = Path("tests/test_mcp.py")
text = path.read_text(encoding="utf-8")
text = text.replace("import okf_parser.cli as cli\n", "from okf_parser import cli\n")
text = text.replace("    from collections.abc import Mapping\n\n", "")
text = text.replace(
    "if TYPE_CHECKING:\n    from fastmcp import FastMCP\n",
    "if TYPE_CHECKING:\n    import pytest\n\n    from fastmcp import FastMCP\n",
)
text = text.replace("monkeypatch: object,", "monkeypatch: pytest.MonkeyPatch,")
text = text.replace("  # type: ignore[attr-defined]", "")
path.write_text(text, encoding="utf-8")
