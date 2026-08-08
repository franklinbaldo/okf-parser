reported as command errors.

MCP tools: `import_preview`; `import_write` with `--allow-write`.

## `init`

```bash
uv run okf-parser init path/to/bundle --spec-template TEMPLATE [--infer-schema] [--write] [--exclude PATTERN]...
```

Scaffolds missing type specification documents at paths derived from
`TEMPLATE`. The template must contain `{slug}`.

- `--spec-template TEMPLATE` — for example `docs/types/{slug}.md`.
- `--infer-schema` — also scaffold a starter `.schema.sql` beside each missing
  specification, inferred from the bundle's observed fields.
- `--write` — create the planned files. Without it the command is a dry run.

Exits `1` when a planned specification or schema path collides with an existing
file that cannot be safely scaffolded.

MCP tools: `init_preview`; `init_write` with `--allow-write`.

## `inventory`

```bash
uv run okf-parser inventory path/to/bundle [--exclude PATTERN]... [--digests]
```

Counts concepts by producer-defined `type`. `--digests` also returns one record
per concept with `concept_id`, `path`, `source_digest`, and `parsed_digest`.
`source_digest` hashes the exact valid UTF-8 source. `parsed_digest` fingerprints
the parser's frontmatter/body value using the RFC 8785/JCS string/object rules
and LF-normalized parsed body under the self-describing
`okf-parsed-v1-jcs-sha256:` prefix. Its object-key order is defined by JCS, not
locale or host-language object enumeration, so numeric-looking and Unicode keys
produce the same digest in Python and TypeScript. It is not a historical
`Revision` id and it does not claim editorial/Markdown semantic equivalence.

MCP tool: `inventory` with the same optional `digests` argument.

## `graph`

```bash
uv run okf-parser graph path/to/bundle [--exclude PATTERN]...
```

Summarizes the resolved concept graph (nodes, edges and broken links) using
NetworkX. Always exits `0`.

MCP tool: `graph`.

## `schema`

```bash
uv run okf-parser schema path/to/bundle [--format json|zod|pydantic] [--infer-types] [--cast FIELD]... [--spec-template TEMPLATE] [--exclude PATTERN]... [--zod-import zod|astro]
```

Exports the bundle's shared frontmatter contract as JSON Schema, Zod source, or
an importable Pydantic v2 module.

The common Pydantic path is intentionally just:
