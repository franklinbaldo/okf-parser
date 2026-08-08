"""Fix the temporary 0.22.0 applicator's generated Python string literal."""

from pathlib import Path

path = Path("scripts/prepare_0_22.py")
text = path.read_text(encoding="utf-8")
old = r'    (tmp_path / "concept.md").write_text("---\ntype: Note\n---\n", encoding="utf-8")'
new = old.replace(r"\n", r"\\n")
if old not in text:
    raise SystemExit("CLI fixture literal not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
