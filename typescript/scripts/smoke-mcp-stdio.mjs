import path from "node:path";
import { fileURLToPath } from "node:url";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

import { PROTOCOL_VERSION } from "../dist/version.js";

const suppliedCommand = process.argv[2];
const command = suppliedCommand === undefined ? process.execPath : path.resolve(suppliedCommand);
const args =
  suppliedCommand === undefined
    ? [fileURLToPath(new URL("../dist/mcp-cli.js", import.meta.url))]
    : [];
const transport = new StdioClientTransport({ command, args, stderr: "pipe" });
const client = new Client({ name: "okf-parser-smoke", version: PROTOCOL_VERSION });

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
