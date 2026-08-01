import { fileURLToPath } from "node:url";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const command = process.argv[2] ?? process.execPath;
const args = process.argv[2]
  ? []
  : [fileURLToPath(new URL("../dist/mcp-cli.js", import.meta.url))];
const transport = new StdioClientTransport({ command, args, stderr: "pipe" });
const client = new Client({ name: "okf-parser-smoke", version: "0.9.0" });

try {
  await client.connect(transport);
  const { tools } = await client.listTools();
  const names = tools.map((tool) => tool.name);
  const expected = ["check", "inventory", "graph", "schema"];
  if (JSON.stringify(names) !== JSON.stringify(expected)) {
    throw new Error(`unexpected MCP tools: ${JSON.stringify(names)}`);
  }
} finally {
  await client.close();
}
