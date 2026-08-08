import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { afterEach, expect, test } from "vitest";

import { createMcpServer } from "../src/mcp.js";
import { PROTOCOL_VERSION } from "../src/version.js";

const openConnections: Array<{ close(): Promise<void> }> = [];

afterEach(async () => {
  await Promise.all(openConnections.splice(0).map((connection) => connection.close()));
});

async function connectedClient(): Promise<Client> {
  const server = createMcpServer();
  const client = new Client({ name: "okf-parser-test", version: PROTOCOL_VERSION });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
  openConnections.push(client, server);
  return client;
}

async function bundle(): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-mcp-"));
  await writeFile(
    path.join(root, "sample.md"),
    "---\ntype: Reference\ntitle: Sample\ncount: 0012\n---\n# Sample\n",
  );
  return root;
}

test("discovers the stable read-only tools", async () => {
  const client = await connectedClient();

  const result = await client.listTools();

  expect(result.tools.map((tool) => tool.name)).toEqual(["check", "inventory", "graph", "schema"]);
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
  const missingPath = "/path/that/does/not/exist";

  const failed = await client.callTool({
    name: "check",
    arguments: { path: missingPath },
  });
  expect(failed.isError).toBe(true);
  expect(failed.content).toEqual([
    expect.objectContaining({ type: "text", text: expect.stringContaining(missingPath) }),
  ]);

  await expect(client.listTools()).resolves.toHaveProperty("tools");
});
