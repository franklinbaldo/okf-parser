from __future__ import annotations

import re
from pathlib import Path


python_path = Path("src/okf_parser/schema_export.py")
source = python_path.read_text(encoding="utf-8")
source = source.replace("import re\n", "", 1)
source = source.replace("import pandas as pd\n", "", 1)
source = source.replace(
    "from okf_parser.bundle import load_bundle\n",
    "from okf_parser.bundle import load_bundle\n"
    "from okf_parser.schema_lexemes import CastKind, can_classify_as, classify_lexemes\n",
    1,
)
source = source.replace(
    'CastKind = Literal["string", "boolean", "integer", "number", "date", "datetime"]\n',
    "",
    1,
)
source = source.replace('_DATE_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}$")\n', "", 1)
source, count = re.subn(
    r"def _series\(.*?\n(?=def _python_type)",
    "",
    source,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"Python inference block replacement count: {count}")
source = source.replace("_can_cast", "can_classify_as")
source = source.replace("_infer_kind", "classify_lexemes")
forbidden = ("pandas", "pd.", "def _series", "def _numeric_kind", "def _temporal_kind")
if any(item in source for item in forbidden):
    raise SystemExit("legacy Python inference remains")
python_path.write_text(source, encoding="utf-8")


ts_path = Path("typescript/src/schema.ts")
source = ts_path.read_text(encoding="utf-8")
anchor = '} from "./core.js";\n'
source = source.replace(
    anchor,
    anchor
    + 'import {\n'
    + '  type LexemeKind,\n'
    + '  canClassifyAs,\n'
    + '  classifyLexemes,\n'
    + '} from "./lexemes.js";\n',
    1,
)
source = source.replace(
    'export type CastKind = "string" | "boolean" | "integer" | "number" | "date" | "datetime";',
    'export type CastKind = LexemeKind;',
    1,
)
source, count = re.subn(
    r'^const (?:INTEGER_RE|NUMBER_RE|DATE_RE|DATETIME_RE) = .*;\n',
    "",
    source,
    flags=re.M,
)
if count != 4:
    raise SystemExit(f"TypeScript scalar constant replacement count: {count}")
source, count = re.subn(
    r"function realDate\(.*?\n(?=function scalarContract)",
    "",
    source,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"TypeScript inference block replacement count: {count}")
source = source.replace("canCast", "canClassifyAs")
source = source.replace("classify(values)", "classifyLexemes(values)")
forbidden = ("Date.parse", "Number.isFinite", "function classify(", "function canCast(")
if any(item in source for item in forbidden):
    raise SystemExit("legacy TypeScript inference remains")
ts_path.write_text(source, encoding="utf-8")


index_path = Path("typescript/src/index.ts")
source = index_path.read_text(encoding="utf-8")
schema_anchor = "export {\n  SchemaCastError,"
lexical_exports = (
    "export {\n"
    "  canClassifyAs,\n"
    "  classifyLexemes,\n"
    "  isIsoDateLexeme,\n"
    "  isIsoDateTimeLexeme,\n"
    '} from "./lexemes.js";\n'
    'export type { LexemeKind } from "./lexemes.js";\n'
)
if schema_anchor not in source:
    raise SystemExit("TypeScript public-export anchor not found")
source = source.replace(schema_anchor, lexical_exports + schema_anchor, 1)
source = source.replace('protocol_version: "0.6.0"', 'protocol_version: "0.7.0"', 1)
index_path.write_text(source, encoding="utf-8")


pyproject_path = Path("pyproject.toml")
source = pyproject_path.read_text(encoding="utf-8")
source = source.replace('version = "0.6.0"', 'version = "0.7.0"', 1)
pyproject_path.write_text(source, encoding="utf-8")
