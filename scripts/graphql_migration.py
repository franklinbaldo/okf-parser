"""One-shot deterministic patch for the GraphQL adapter branch."""

from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected exactly one replacement anchor")
    target.write_text(text.replace(old, new), encoding="utf-8")


replace_once(
    "src/okf_parser/graphql_adapter.py",
    '    if isinstance(node, ScalarNode) and _scalar_graphql_type(node) == "BigInt":\n'
    "        return str(value)\n"
    "    return _json_ready(value)\n",
    "    result = _json_ready(value)\n"
    "    if isinstance(node, ScalarNode):\n"
    "        scalar = _scalar_graphql_type(node)\n"
    '        if scalar == "BigInt":\n'
    "            result = str(value)\n"
    '        elif scalar == "Date" and isinstance(value, datetime):\n'
    "            result = value.date().isoformat()\n"
    '        elif scalar == "Date" and isinstance(value, date):\n'
    "            result = value.isoformat()\n"
    "    return result\n",
)

replace_once(
    "pyproject.toml",
    "\n[dependency-groups]\ndev = [\n",
    '\n[project.optional-dependencies]\ngraphql = ["graphql-core>=3.2,<4"]\n\n'
    '[dependency-groups]\ndev = [\n  "graphql-core>=3.2,<4",\n',
)
replace_once(
    "pyproject.toml",
    '"src/okf_parser/duckdb.py" = ["PLR0913"]\n',
    '"src/okf_parser/duckdb.py" = ["PLR0913"]\n'
    '"src/okf_parser/graphql_adapter.py" = ["PLR0913"]\n',
)

replace_once(
    "src/okf_parser/service.py",
    "from okf_parser.formatting import FormatReport, format_path\n",
    "from okf_parser.formatting import FormatReport, format_path\n"
    "from okf_parser.graphql_adapter import export_graphql_sdl\n",
)
replace_once(
    "src/okf_parser/service.py",
    '    """Export JSON Schema, Zod, or importable Pydantic source."""\n'
    '    if fmt == "zod":\n',
    '    """Export JSON Schema, Zod, Pydantic source, or deterministic GraphQL SDL."""\n'
    '    if fmt == "graphql":\n'
    "        return export_graphql_sdl(\n"
    "            path,\n"
    "            exclude,\n"
    "            infer_types=infer_types,\n"
    "            casts=casts,\n"
    "            spec_template=spec_template,\n"
    "        )\n"
    '    if fmt == "zod":\n',
)

replace_once(
    "src/okf_parser/cli.py",
    'type SchemaFormat = Literal["json", "zod", "pydantic"]\n',
    'type SchemaFormat = Literal["json", "zod", "pydantic", "graphql"]\n',
)
replace_once(
    "src/okf_parser/cli.py",
    '    """Export JSON Schema, Zod, or importable Pydantic source."""\n',
    '    """Export JSON Schema, Zod, Pydantic source, or GraphQL SDL."""\n',
)

replace_once(
    "src/okf_parser/__init__.py",
    "from okf_parser.ingestion import DocumentEnvelope, IngestionCapability, ingest_documents\n",
    "from okf_parser.graphql_adapter import (\n"
    "    GraphQLAdapterUnavailableError,\n"
    "    GraphQLNameCollisionError,\n"
    "    GraphQLReadAdapter,\n"
    "    GraphQLResult,\n"
    "    build_graphql_schema,\n"
    "    export_graphql_sdl,\n"
    ")\n"
    "from okf_parser.ingestion import DocumentEnvelope, IngestionCapability, ingest_documents\n",
)
replace_once(
    "src/okf_parser/__init__.py",
    '    "IngestionCapability",\n',
    '    "GraphQLAdapterUnavailableError",\n'
    '    "GraphQLNameCollisionError",\n'
    '    "GraphQLReadAdapter",\n'
    '    "GraphQLResult",\n'
    '    "IngestionCapability",\n',
)
replace_once(
    "src/okf_parser/__init__.py",
    '    "ingest_documents",\n',
    '    "build_graphql_schema",\n'
    '    "export_graphql_sdl",\n'
    '    "ingest_documents",\n',
)

OLD_VERSION = "0.42.8"
NEW_VERSION = "0.43.0"
VERSION_FILES = (
    "pyproject.toml",
    "rust-core/Cargo.toml",
    "typescript/package.json",
    "typescript/src/version.ts",
    "typescript-duckdb/package.json",
    "native-npm-linux-x64/package.json",
    "README.md",
)
for version_path in VERSION_FILES:
    target = Path(version_path)
    text = target.read_text(encoding="utf-8")
    if OLD_VERSION not in text:
        raise RuntimeError(f"{version_path}: version anchor {OLD_VERSION} not found")
    target.write_text(text.replace(OLD_VERSION, NEW_VERSION), encoding="utf-8")

changelog = Path("changelog/0.43.0.md")
if changelog.exists():
    raise RuntimeError("changelog/0.43.0.md already exists")
changelog.write_text(
    """# 0.43.0

- Add deterministic GraphQL SDL generation from the shared `TypeContract`.
- Add the optional `okf-parser[graphql]` embedded read-only executable adapter.
- Resolve `concept` and bounded, stably ordered `concepts` queries over canonical Ibis relations.
- Reuse public RFC 0006 typed relations for declared scalar/list values, including exact
  BigInt, Decimal, Date, DateTime, UUID and JSON projection policies.
- Expose forward/reverse links, diagnostics, source/parsed digests and raw frontmatter.
- Fail explicitly on GraphQL naming collisions and preserve authored type/field provenance
  through SDL directives.
- Keep GraphQL transport host-owned: no HTTP server and no mutations are introduced.
""",
    encoding="utf-8",
)
