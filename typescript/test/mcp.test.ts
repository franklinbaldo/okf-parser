import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { describe, expect, test } from "vitest";

import { createMcpServer } from "../src/mcp.js";

async function bundle(): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), "okf-parser-mcp-"));
  await mkdir(path.join(root, "specs"));
  await writeFile(path.join(root, "sample.md"), "---\ntype: Sample\nvalue: 1\n---\n# Sample\n", "utf8");
  await writeFile(path.join(root, "specs", "sample.md"), "# Sample\n", "utf8");
  return root;
}

async function connectedClient(): Promise<Client> {
  const server = createMcpServer();
  const client = new Client({ name: "okf-parser-tests", version: "1" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  return client;
}

describe("MCP server", () => {
  test("discovers the stable read-only tools", async () => {
    const client = await connectedClient();
    const tools = await client.listTools();
    expect(tools.tools.map((tool) => tool.name).sort()).toEqual([
      "check",
      "format_check",
      "graph",
      "inventory",
      "schema",
    ]);
  });

  test("returns structured validation and schema results", async () => {
    const client = await connectedClient();
    const root = await bundle();

    const check = await client.callTool({
      name: "check",
      arguments: { path: root, classify: true },
    });
    expect(check.isError).not.toBe(true);
    expect(check.structuredContent).toMatchObject({
      conformant: true,
      concept_count: 1,
      classification: {
        concepts: ["sample.md"],
        reserved: [],
        ignored: [],
        invalid_or_untyped: [],
      },
    });

    const inventory = await client.callTool({
      name: "inventory",
      arguments: { path: root, digests: true },
    });
    expect(inventory.structuredContent).toMatchObject({
      digests: [
        {
          concept_id: "sample",
          path: "sample.md",
          source_digest: expect.stringMatching(/^sha256:/u),
          parsed_digest: expect.stringMatching(/^okf-parsed-v1-jcs-sha256:/u),
        },
      ],
    });

    const schema = await client.callTool({
      name: "schema",
      arguments: { path: root, infer_types: true },
    });
    expect(schema.structuredContent).toMatchObject({
      total_types: 1,
      inferred_types: true,
    });
  });

  test("reports domain failures as tool errors without closing the session", async () => {
    const client = await connectedClient();
    const missing = await client.callTool({
      name: "check",
      arguments: { path: "/definitely/missing/okf-bundle" },
    });
    expect(missing.isError).toBe(true);

    const root = await bundle();
    const valid = await client.callTool({ name: "check", arguments: { path: root } });
    expect(valid.isError).not.toBe(true);
  });
});
