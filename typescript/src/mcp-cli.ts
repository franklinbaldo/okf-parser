#!/usr/bin/env node

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { createMcpServer } from "./mcp.js";

const server = createMcpServer();

async function close(): Promise<void> {
  await server.close();
}

process.once("SIGINT", () => {
  void close().finally(() => {
    process.exitCode = 130;
  });
});
process.once("SIGTERM", () => {
  void close().finally(() => {
    process.exitCode = 143;
  });
});

server.connect(new StdioServerTransport()).catch((error: unknown) => {
  const message = error instanceof Error ? error.stack ?? error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
