---
type: Documentation
title: okf-parser command reference
description: Every CLI command and MCP tool exposed by okf-parser, with flags and exit codes
---

# okf-parser command reference

`okf-parser` exposes the same operations two ways: as CLI commands (Cyclopts,
`uv run okf-parser <command>`) and as read-only MCP tools (`okf-parser-mcp`,
or `okf-parser serve`). Both wrap the same functions in
`okf_parser.service`, so behavior and JSON payload shape are identical
between the two surfaces.

All commands accept a `path` to a bundle root. All except `serve` accept
`--exclude` (repeatable) to skip subpaths in addition to any `.okfignore` —
see [Excluding paths](../README.md#excluding-paths) in the README.

Every command prints a single JSON object to stdout (or plain text where
noted) and exits non-zero exactly when the payload signals failure. There is
no other side channel: warnings, diagnostics and results all live in that one
payload.

## `check`

```bash
uv run okf-parser check path/to/bundle [--exclude PATTERN]... [--require-spec NAME] [--normative-spec]
```

Validates every Markdown file recursively as OKF v0.2. Exits `1` only when
`payload["conformant"]` is `false` — normative errors, not broken
cross-links, which OKF v0.2 defines as advisory.

- `--require-spec NAME` — fail unless every concept declares this type
  specification.
- `--normative-spec` — treat a missing/mismatched type specification as a
  normative error rather than an advisory diagnostic.

MCP tool: `check`.

## `inventory`

```bash
uv run okf-parser inventory path/to/bundle [--exclude PATTERN]...
```

Counts concepts by type using an Ibis relation over the parsed bundle.
Always exits `0`.

MCP tool: `inventory`.

## `graph`

```bash
uv run okf-parser graph path/to/bundle [--exclude PATTERN]...
```

Summarizes the resolved concept graph (nodes, edges, broken links) using
NetworkX. Always exits `0`.

MCP tool: `graph`.

## `schema`

```bash
uv run okf-parser schema path/to/bundle [--format json|zod] [--infer-types] [--cast FIELD]... [--exclude PATTERN]... [--zod-import zod|astro]
```

Exports a canonical JSON Schema or Zod schema for the bundle's concept types.

- `--format` — `json` (default) or `zod`.
- `--infer-types` — infer scalar types from observed frontmatter values
  instead of leaving them untyped.
- `--cast FIELD` — declare a scalar type for a specific field, repeatable.
- `--zod-import` — only meaningful with `--format zod`; choose the `zod` or
  `astro:content` import style.

MCP tool: `schema` (same flags, `format` passed as `schema_format`).

## `format`

```bash
uv run okf-parser format path/to/bundle [--write] [--exclude PATTERN]...
```

Checks (or, with `--write`, applies) canonical Markdown formatting. Exits `1`
when `payload["succeeded"]` is `false` — i.e. files would change and
`--write` was not passed. Files whose protected block structure the rewrite
would change are left on disk and listed in `payload["skipped_paths"]`; see
[Formatting](../README.md#quick-start) in the README for what "protected"
means.

MCP tool: `format_check` (read-only; there is no MCP tool that writes files).

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

No MCP equivalent — materializing a file is a write operation, and the MCP
surface is read-only by design.

## `serve`

```bash
uv run okf-parser serve [--transport stdio|http|sse] [--host HOST] [--port PORT]
```

Runs the MCP server. `stdio` (default) is what `okf-parser-mcp` runs
directly; `http`/`sse` bind `--host`/`--port` for network transports. Serves
`check`, `inventory`, `graph`, `schema` and `format_check` — every read-only
operation above except `duckdb`.
