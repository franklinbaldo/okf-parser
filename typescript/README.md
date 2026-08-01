---
type: Documentation
title: okf-parser for TypeScript
description: Native TypeScript package usage and capability scope
---

# okf-parser for TypeScript

Native ESM implementation of the OKF parser and validator. It follows the same
observable contract as the Python package while using an idiomatic immutable
TypeScript core.

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

Parsing, validation, inventory, graph summaries, JSON Schema, Zod generation and
MCP are stable capabilities in this package. DuckDB materialization is provided
by the optional `okf-parser-duckdb` sibling package. Formatter support remains
`not_implemented` in TypeScript.
