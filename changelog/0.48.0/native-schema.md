---
type: Release Note
title: schema runs in the binary
---

# schema runs in the binary

Phase 6b of [RFC 0024](../../rfcs/0024-rust-native-core.md). `okf-parser
schema` and the MCP `schema` tool run `okf_db::schema`: observed frontmatter,
`--infer-types` and `--cast`, declared `.schema.sql` types, relational
references (`--relational-schema`, `--refs key|embed`) and `type: Projection`
documents compile to one contract set, which the binary renders as JSON
Schema, Zod or GraphQL SDL. Output is byte-for-byte what 0.47 printed; the
shared `conformance/schema-output.json` and `schema-inference.json` corpora
pin it.

Pydantic stays in Python, where its naming rules live: `--format pydantic`
pipes the contracts to `python -m okf_parser.pydantic_source` (`OKF_PYTHON`
picks the interpreter), and `build_pydantic_models()`,
`export_pydantic_source()` and `GraphQLReadAdapter` decode the same contracts
from the binary. The MCP server no longer delegates any tool, so
`okf_parser.mcp_bridge` is gone.

Removed Python internals: `okf_parser.projections`,
`okf_parser.projection_export`, `okf_parser.schema_references`,
`okf_parser.schema_lexemes`, and the compiler and renderers in
`okf_parser.schema_contract` (`compile_contracts`, `contract_json_schema`,
`node_json_schema`, `node_zod`, `render_zod`, `parse_casts`,
`unique_model_names`) and `okf_parser.graphql_adapter.render_graphql_sdl`.
The `export_*`, `build_*` and `schema_bundle()` entry points keep their
signatures; `SchemaReferenceError` and `ProjectionError` now live in
`okf_parser.schema_contract`.
