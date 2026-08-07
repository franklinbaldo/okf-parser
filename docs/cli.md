---
type: Documentation
title: okf-parser command reference
description: Every CLI command and MCP tool exposed by okf-parser, with flags and exit codes
---

# okf-parser command reference

`okf-parser` exposes its command-line interface through Cyclopts
(`uv run okf-parser <command>`). Read-only operations are also exposed through
FastMCP (`okf-parser-mcp`, or `okf-parser serve`) and share the same service
functions and payloads where both surfaces exist.

The MCP surface is intentionally read-only. It exposes `check`, `inventory`,
`graph`, `schema` and `format_check`; it does not expose `import`, `init`,
`apply` or `duckdb`, all of which can create or mutate files.

Most commands print one JSON object to stdout. `schema --format zod` prints the
Zod source as plain text. Exit status is command-specific and described below.

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

No MCP equivalent.

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

No MCP equivalent.

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
uv run okf-parser schema path/to/bundle [--format json|zod] [--infer-types] [--cast FIELD]... [--spec-template TEMPLATE] [--exclude PATTERN]... [--zod-import zod|astro]
```

Exports a canonical JSON Schema or Zod schema for the bundle's concept types.

- `--format` — `json` (default) or `zod`.
- `--infer-types` — infer scalar types from observed frontmatter values instead
  of leaving them untyped.
- `--cast FIELD` — declare a scalar type for a specific field, repeatable.
- `--spec-template TEMPLATE` — derive each type's specification path and, when
  present, read the sibling `.schema.sql` declaration introduced by RFC 0006.
  The template must contain `{slug}`.
- `--zod-import` — only meaningful with `--format zod`; choose the `zod` or
  `astro:content` import style.

MCP tool: `schema` (same flags, with `format` exposed as `schema_format` in the
Python function signature).

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

MCP tool: `format_check` (read-only; there is no MCP tool that writes files).

## `apply`

```bash
uv run okf-parser apply path/to/bundle --sql SQL [--write] [--exclude PATTERN]...
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

`--write` is required to touch the bundle. Without it the command computes and
reports the candidate changes. Exits `1` when `payload["succeeded"]` is false,
including validation or write-conflict failures.

No MCP equivalent.

## `duckdb`

```bash
uv run okf-parser duckdb path/to/bundle [database] [schema] [--overwrite] [--exclude PATTERN]...
```

Materializes the bundle's concepts and links into a DuckDB database file.

- `database` — positional, defaults to `okf.duckdb`.
- `schema` — positional, defaults to `okf`.
- `--overwrite` — replace existing tables in that schema instead of failing.

On a name collision without `--overwrite`, exits `1` with
`{"error", "schema", "existing_tables"}` in the payload instead of raising.

No MCP equivalent.

## `serve`

```bash
uv run okf-parser serve [--transport stdio|http|sse] [--host HOST] [--port PORT]
```

Runs the MCP server. `stdio` (default) is what `okf-parser-mcp` runs directly;
`http` and `sse` bind `--host` and `--port` for network transports. Serves the
five read-only tools listed above.
