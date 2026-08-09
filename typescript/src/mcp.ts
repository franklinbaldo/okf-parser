import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod/v4";

import { checkBundle, graphBundle, inventoryBundle } from "./core.js";
import { exportJsonSchema, exportZod } from "./schema.js";
import { PROTOCOL_VERSION } from "./version.js";

const pathSchema = z.string().min(1).describe("Path to the OKF bundle root");
const excludeSchema = z
  .array(z.string())
  .optional()
  .describe("Anchored bundle-relative exclusion patterns");

const requireSpecSchema = z
  .string()
  .min(1)
  .optional()
  .describe('Specification document template per type, e.g. ".okf/specs/{slug}.md"');
const normativeSpecSchema = z
  .boolean()
  .optional()
  .describe("Report a missing specification document as an error instead of a warning");
const classifySchema = z
  .boolean()
  .optional()
  .describe("Explain concept, reserved, ignored, and invalid bundle membership");
const digestsSchema = z
  .boolean()
  .optional()
  .describe("Include deterministic source and parsed content digests");

function loadOptions(exclude: readonly string[] | undefined) {
  return exclude === undefined ? {} : { exclude };
}

function structuredResult(value: unknown) {
  const text = JSON.stringify(value, null, 2);
  const structuredContent = JSON.parse(text) as Record<string, unknown>;
  return {
    content: [{ type: "text" as const, text }],
    structuredContent,
  };
}

function textResult(text: string) {
  return { content: [{ type: "text" as const, text }] };
}

async function toolResult<T>(operation: () => Promise<T>) {
  try {
    const result = await operation();
    return typeof result === "string" ? textResult(result) : structuredResult(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      content: [{ type: "text" as const, text: message }],
      isError: true,
    };
  }
}

export function createMcpServer(): McpServer {
  const server = new McpServer(
    { name: "okf-parser", version: PROTOCOL_VERSION },
    {
      instructions:
        "Read-only tools for validating and inspecting Open Knowledge Format bundles. " +
        "Call check before relying on inventory, graph, or schema output. No tool writes files.",
    },
  );

  server.registerTool(
    "check",
    {
      title: "Validate OKF bundle",
      description: "Validate every authored Markdown document below an OKF bundle root.",
      inputSchema: z.object({
        path: pathSchema,
        exclude: excludeSchema,
        require_spec: requireSpecSchema,
        normative_spec: normativeSpecSchema,
        classify: classifySchema,
      }),
    },
    ({ path, exclude, require_spec, normative_spec, classify }) =>
      toolResult(() =>
        checkBundle(path, {
          ...loadOptions(exclude),
          ...(require_spec === undefined ? {} : { requireSpec: require_spec }),
          normativeSpec: normative_spec ?? false,
          classify: classify ?? false,
        }),
      ),
  );

  server.registerTool(
    "inventory",
    {
      title: "Inventory OKF concepts",
      description: "Count parsed concepts by their producer-defined type.",
      inputSchema: z.object({ path: pathSchema, exclude: excludeSchema, digests: digestsSchema }),
    },
    ({ path, exclude, digests }) =>
      toolResult(() => inventoryBundle(path, { ...loadOptions(exclude), digests: digests ?? false })),
  );

  server.registerTool(
    "graph",
    {
      title: "Summarize OKF graph",
      description: "Summarize resolved concept links and graph connectivity.",
      inputSchema: z.object({ path: pathSchema, exclude: excludeSchema }),
    },
    ({ path, exclude }) => toolResult(() => graphBundle(path, loadOptions(exclude))),
  );

  server.registerTool(
    "schema",
    {
      title: "Export OKF schema",
      description: "Export canonical JSON Schema or Zod declarations for observed concept types.",
      inputSchema: z.object({
        path: pathSchema,
        format: z.enum(["json", "zod"]).optional().default("json"),
        infer_types: z.boolean().optional().default(false),
        cast: z.array(z.string()).optional(),
        exclude: excludeSchema,
        zod_import: z.enum(["zod", "astro"]).optional().default("zod"),
      }),
    },
    ({ path, format, infer_types, cast, exclude, zod_import }) => {
      const common = {
        ...loadOptions(exclude),
        inferTypes: infer_types,
        ...(cast === undefined ? {} : { casts: cast }),
      };
      if (format === "zod") {
        return toolResult(() => exportZod(path, { ...common, zodImport: zod_import }));
      }
      return toolResult(() => exportJsonSchema(path, common));
    },
  );

  return server;
}
