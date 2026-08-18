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
    "    GraphQLAdapterUnavailable,\n"
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
    '    "GraphQLAdapterUnavailable",\n'
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
