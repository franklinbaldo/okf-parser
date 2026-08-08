"""Fix the temporary 0.23.0 applicator's ParsedDocument interface anchor."""

from pathlib import Path

path = Path("scripts/prepare_0_23.py")
text = path.read_text(encoding="utf-8")
old = '''replace(
    "typescript/src/core.ts",
    ''' + "'''" + '''  readonly description: string | null;\\n  readonly frontmatterJson: string;\\n  readonly body: string;\\n}\\n\\nexport interface ConceptRecord {\\n''' + "'''" + ''',
    ''' + "'''" + '''  readonly description: string | null;\\n  readonly sourceDigest: string;\\n  readonly revisionDigest: string;\\n  readonly frontmatterJson: string;\\n  readonly body: string;\\n}\\n\\nexport interface ConceptRecord {\\n''' + "'''" + ''',
)
'''
new = '''replace(
    "typescript/src/core.ts",
    ''' + "'''" + '''  readonly frontmatterJson: string;\\n  readonly body: string;\\n  readonly conceptType: string;\\n  readonly title: string | null;\\n  readonly description: string | null;\\n}\\n\\nexport interface ConceptRecord {\\n''' + "'''" + ''',
    ''' + "'''" + '''  readonly frontmatterJson: string;\\n  readonly body: string;\\n  readonly conceptType: string;\\n  readonly title: string | null;\\n  readonly description: string | null;\\n  readonly sourceDigest: string;\\n  readonly revisionDigest: string;\\n}\\n\\nexport interface ConceptRecord {\\n''' + "'''" + ''',
)
'''
if old not in text:
    raise SystemExit("ParsedDocument replacement block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
