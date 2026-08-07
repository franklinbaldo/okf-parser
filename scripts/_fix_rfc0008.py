from pathlib import Path

path = Path("tests/test_mcp.py")
text = path.read_text(encoding="utf-8")
text = text.replace("import okf_parser.cli as cli\n", "from okf_parser import cli\n")
text = text.replace("    from collections.abc import Mapping\n\n", "")
path.write_text(text, encoding="utf-8")
