---
type: Documentation
title: okf-parser DuckDB adapter
description: Optional native DuckDB materialization for the TypeScript parser
---

# okf-parser-duckdb

Optional DuckDB materialization adapter for the TypeScript `okf-parser` package.
It uses the official promise-native `@duckdb/node-api` client and remains a
separate package so native database bindings never burden parser-only users.

```bash
npm install okf-parser okf-parser-duckdb
```

```ts
import { DuckDBConnection } from "@duckdb/node-api";
import { attachOkf } from "okf-parser-duckdb";

const connection = await DuckDBConnection.create();
const report = await attachOkf(connection, "./knowledge");
```

To create or update a persistent database file:

```ts
import { exportDuckDb } from "okf-parser-duckdb";

const report = await exportDuckDb("./knowledge", {
  database: "knowledge.duckdb",
  schema: "okf",
});
```

Or use the CLI:

```bash
npx --package okf-parser-duckdb okf-parser-ts-duckdb ./knowledge \
  --database knowledge.duckdb
```

The adapter creates four ordinary tables inside the selected schema:
`concepts`, `links`, `reserved`, and `diagnostics`. Existing tables are never
replaced unless `overwrite: true` or `--overwrite` is explicit.
