"""Fix the temporary 0.23.0 applicator's inventory docs anchor."""

from pathlib import Path

path = Path("scripts/prepare_0_23.py")
text = path.read_text(encoding="utf-8")
old = "    '''Counts concepts by producer-defined `type`.\\n\\nMCP tool: `inventory`.\\n''',\n"
new = (
    "    '''Counts concepts by type using an Ibis relation over the parsed bundle. Always\\n"
    "exits `0`.\\n\\nMCP tool: `inventory`.\\n''',\n"
)
if old not in text:
    raise SystemExit("inventory docs anchor not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
