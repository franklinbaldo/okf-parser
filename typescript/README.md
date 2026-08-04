---
type: Documentation
title: okf-parser for TypeScript
description: Native TypeScript package usage and capability scope
---

# okf-parser for TypeScript

Native ESM implementation of the OKF parser, validator and formatter. It follows
the same observable contract as the Python package while using an idiomatic
immutable TypeScript core.

```bash
npm install okf-parser
npx okf-parser-ts check ./knowledge
```

```ts
import { checkBundle, exportZod, parseDocumentContent } from "okf-parser";

const parsed = parseDocumentContent(`---\ntype: Reference\ncount: 0012\n---\n`);
const report = await checkBundle("./knowledge");
const zod = await exportZod("./knowledge", { inferTypes: true });
```

## Requiring a specification per type

The optional rule derives a document path from every producer-defined type in
use and reports the types whose document is absent. The slug is lowercase, with
accents and cedillas removed, whitespace and `/` turned into hyphens, and every
remaining non-alphanumeric character dropped, so `Revisão Ciência` resolves to
`.okf/specs/revisao-ciencia.md`.

```bash
npx okf-parser-ts check ./knowledge --require-spec ".okf/specs/{slug}.md"
npx okf-parser-ts check ./knowledge --require-spec ".okf/specs/{slug}.md" --normative-spec
```

```ts
const report = await checkBundle("./knowledge", { requireSpec: ".okf/specs/{slug}.md" });
```

Missing documents are `OKF010` warnings unless `--normative-spec` promotes them
to errors. The slug derivation is shared with the Python package through
`conformance/type-spec-slugs.json`.

## Markdown formatting

Formatting is read-only unless `--write` is explicit:

```bash
npx --package okf-parser okf-parser-ts format ./knowledge
npx --package okf-parser okf-parser-ts format ./knowledge --write
```

The library exposes both the pure one-document formatter and the filesystem
operation:

```ts
import { formatMarkdown, formatPath } from "okf-parser";

const canonical = formatMarkdown("# Heading\n\n-   item\n");
const report = await formatPath("./knowledge", { write: true });
```

The formatter parses CommonMark, GFM and YAML frontmatter into a public mdast
syntax tree, serializes canonically, and refuses a rewrite when the protected
block signature changes. Ordered lists are consecutive without zero-padding,
code/frontmatter/raw HTML content is protected, and all candidate files are
formatted before the first write.

## MCP server

The package includes a read-only stdio MCP server:

```bash
npx --package okf-parser okf-parser-ts-mcp
```

A typical host configuration is:

```json
{
  "servers": {
    "okf-parser": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "--package", "okf-parser", "okf-parser-ts-mcp"]
    }
  }
}
```

Applications embedding the server can import the factory without starting a
transport:

```ts
import { createMcpServer } from "okf-parser/mcp";

const server = createMcpServer();
```

The MCP adapter exposes `check`, `inventory`, `graph` and `schema`. All tools are
read-only and reuse the same core functions as the CLI and package API.

## Optional DuckDB adapter

DuckDB support is published separately so native database bindings are never
installed for parser-only consumers:

```bash
npm install okf-parser okf-parser-duckdb
```

```ts
import { exportDuckDb } from "okf-parser-duckdb";

await exportDuckDb("./knowledge", { database: "knowledge.duckdb" });
```

Parsing, validation, formatting, inventory, graph summaries, JSON Schema, Zod
generation and MCP are stable capabilities in this package. DuckDB
materialization is provided by the optional `okf-parser-duckdb` sibling package.
