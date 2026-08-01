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

The package currently exposes parsing, validation, inventory, graph summaries,
JSON Schema and Zod generation. Formatter, DuckDB and MCP support remain
explicitly marked `not_implemented` in the exported capability manifest.
