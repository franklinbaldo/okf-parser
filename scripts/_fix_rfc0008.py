from pathlib import Path

path = Path("tests/test_mcp.py")
text = path.read_text(encoding="utf-8")
text = text.replace("import okf_parser.cli as cli\n", "from okf_parser import cli\n")
text = text.replace("    from collections.abc import Mapping\n\n", "")
text = text.replace("from fastmcp import Client\n", "from fastmcp import Client\nfrom pytest import MonkeyPatch\n")
text = text.replace("monkeypatch: object,", "monkeypatch: MonkeyPatch,")
text = text.replace("  # type: ignore[attr-defined]", "")
path.write_text(text, encoding="utf-8")
