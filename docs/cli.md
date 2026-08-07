---
type: Documentation
title: okf-parser command reference
description: Every CLI command and MCP tool exposed by okf-parser, with flags and exit codes
---

# okf-parser command reference

`okf-parser` exposes its command-line interface through Cyclopts
(`uv run okf-parser <command>`). Inspection operations are also exposed through
FastMCP (`okf-parser-mcp`, or `okf-parser serve`) and share the same service
functions and payloads where both surfaces exist.

The default MCP profile is commit-disabled and exposes inspection plus faithful
preview tools. `okf-parser serve --allow-write` adds explicit commit tools; the
zero-argument `okf-parser-mcp` entry point remains commit-disabled. Tool-level MCP
annotations describe each tool's maximum possible effect and are descriptive hints,
not an authorization boundary.

Most commands print one JSON object to stdout. `schema --format zod` and
`schema --format pydantic` print source as plain text. Text output preserves an
existing final newline instead of adding a second one. Exit status is command-specific
and described below.

Commands that walk an existing bundle accept `--exclude` (repeatable) where
shown, in addition to any `.okfignore`; see
[Excluding paths](../README.md#excluding-paths) in the README.

## `check`

```bash
uv run okf-parser check path/to/bundle [--exclude PATTERN]... [--require-spec TEMPLATE] [--normative-spec]
```

Validates every Markdown file recursively as OKF v0.2. Exits `1` only when
`payload["conformant"]` is `false` — normative errors, not advisory broken
cross-links or missing type specifications.

- `--require-spec TEMPLATE` — derive the expected specification document for
  every concept type. The template must contain `{slug}`, for example
  `docs/types/{slug}.md`.
- `--normative-spec` — promote missing or mismatched required specifications
  from advisory diagnostics to normative errors.

MCP tool: `check`.

## `import`

```bash
uv run okf-parser import SOURCE path/to/bundle --type TYPE [--id-column COLUMN] [--write] [--overwrite]
```

Materializes every row of a DuckDB-readable source such as CSV, Parquet or JSON
as one concept document of `TYPE`.

- `SOURCE` — input readable by DuckDB.
- `--type TYPE` — canonical concept `type` written to every imported document;
  a source column named `type` is reserved and rejected.
- `--id-column COLUMN` — derive destination concept ids from this source column;
  otherwise row position is used.
- `--write` — actually create files. Without it the command is a dry run.
- `--overwrite` — permit replacement of an existing destination; without it,
  collisions are reported rather than silently replaced.

Exits `1` when the import plan contains duplicate ids. Other invalid inputs are
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
uv run okf-parser inventory path/to/bundle [--exclude PATTERN]...
```

Counts concepts by type using an Ibis relation over the parsed bundle. Always
exits `0`.

MCP tool: `inventory`.

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

```bash
uv run okf-parser schema path/to/bundle --format pydantic
```

No alias configuration, naming policy, profile file, or second code-generation
command is required. The compiler automatically maps authored YAML keys that are
unsafe as Pydantic attributes — including Python keywords, leading-underscore
keys and protected `model_*` names — while preserving the authored key as the
validation alias. Truly ambiguous name collisions fail with the conflicting
keys or structural paths in the error instead of requiring preconfiguration.

Generated internal Pydantic identifiers are also bounded deterministically when
valid OKF keys or concept types are very long. A stable digest suffix preserves
identity, and long aliases, literals and annotations are emitted in canonical
multiline form rather than relying on a later formatter pass.

- `--format` — `json` (default), `zod`, or `pydantic`.
- `--infer-types` — infer scalar types from observed frontmatter values instead
  of leaving them untyped.
- `--cast FIELD` — declare a scalar type for a specific field, repeatable.
- `--spec-template TEMPLATE` — derive each type's specification path and, when
  present, read the sibling `.schema.sql` declaration introduced by RFC 0006.
  The template must contain `{slug}`.
- `--zod-import` — only meaningful with `--format zod`; choose the `zod` or
  `astro:content` import style.

The Pydantic target prints one deterministic source module to stdout, so normal
shell redirection is enough when a file is desired:

```bash
uv run okf-parser schema path/to/bundle --format pydantic > generated_models.py
```

The redirected bytes are the renderer bytes: if the source already ends in its
canonical newline, the CLI does not append a second blank line. Checked-in short
and adversarial source snapshots exercise this contract against the repository's
Ruff formatting and lint rules.

MCP tool: `schema` with the same `json`, `zod`, and `pydantic` format choices
(`format` is represented internally by the Python parameter `schema_format`).

## `format`

```bash
uv run okf-parser format path/to/bundle [--write] [--exclude PATTERN]...
```

Checks, or with `--write` applies, canonical Markdown formatting. Exits `1`
when `payload["succeeded"]` is `false` — for example, files would change and
`--write` was not passed. Files whose protected block structure the rewrite
would change are left on disk and listed in `payload["skipped_paths"]`; see
[Formatting](../README.md#quick-start) in the README for what "protected"
means.

MCP tools: `format_check`; `format_write` with `--allow-write`.

## `apply`

```bash
uv run okf-parser apply path/to/bundle --sql SQL [--write] [--spec-template TEMPLATE] [--exclude PATTERN]...
```

or, for the simple replace-one-field case:

```bash
uv run okf-parser apply path/to/bundle --type TYPE --field FIELD --from VALUE --to VALUE [--write] [--exclude PATTERN]...
```

Mutates concept frontmatter through RFC 0005's bounded relational write path.
The SQL form accepts zero or more leading `ALTER TABLE` statements followed by
exactly one `UPDATE`; DuckDB parses, binds and executes the script, and the
final relational state is compiled back into the affected documents.

The `--type/--field/--from/--to` form is convenience syntax for a simple value
replacement without hand-writing SQL.

`--spec-template TEMPLATE` opts declared fields into RFC 0006's typed query
surface. Each declared value keeps a compiler-owned raw carrier and appears to SQL
as a DuckDB virtual generated `TRY_CAST` column. Declared columns are queryable but
not directly writable; undeclared scalar fields retain RFC 0005 write semantics.

`--write` is required to touch the bundle. Without it the command computes and
reports the candidate changes. Exits `1` when `payload["succeeded"]` is false,
including validation or write-conflict failures.

MCP tools: `apply_preview`; `apply_write` with `--allow-write`. Both accept `spec_template`.

## `duckdb`

```bash
uv run okf-parser duckdb path/to/bundle [database] [schema] [--overwrite] [--spec-template TEMPLATE] [--exclude PATTERN]...
```

Materializes the bundle's concepts and links into a DuckDB database file.

- `database` — positional, defaults to `okf.duckdb`.
- `schema` — positional, defaults to `okf`.
- `--overwrite` — replace existing tables in that schema instead of failing.
- `--spec-template TEMPLATE` — execute each present sibling `.schema.sql` and
  materialize declared concept types into a second `{schema}_types` schema.
  Declared values keep a compiler-owned raw column beside a materialized typed
  `TRY_CAST` projection; types without a declaration remain available only in
  the complete untyped `concepts` table.

On a name collision without `--overwrite`, exits `1` with
`{"error", "schema", "existing_tables"}` in the payload instead of raising.
The MCP `duckdb_export` tool preserves that same structured collision payload.

MCP tool: `duckdb_export` with `--allow-write`; it also accepts `spec_template`.

## `serve`

```bash
uv run okf-parser serve [--transport stdio|http|sse] [--host HOST] [--port PORT] [--allow-write]
```

Runs the MCP server. `stdio` (default) is what `okf-parser-mcp` runs directly;
`http` and `sse` bind `--host` and `--port` for network transports. The default
profile exposes `check`, `inventory`, `graph`, `schema`, `format_check`,
`apply_preview`, `init_preview`, and `import_preview`. `--allow-write` additionally
exposes `format_write`, `apply_write`, `init_write`, `import_write`, and
`duckdb_export`. Because `schema` and `apply_preview` may execute trusted RFC 0006
`.schema.sql`, commit-disabled is intentionally not advertised as globally
side-effect-free.
