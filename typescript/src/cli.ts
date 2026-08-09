#!/usr/bin/env node

import { parseArgs } from "node:util";

import {
  OkfParserError,
  checkBundle,
  exportJsonSchema,
  exportZod,
  formatPath,
  graphBundle,
  inventoryBundle,
} from "./index.js";

const USAGE = `Usage:
  okf-parser-ts check PATH [--exclude PATTERN ...] [--require-spec TEMPLATE] [--normative-spec] [--classify]
  okf-parser-ts inventory PATH [--exclude PATTERN ...] [--digests]
  okf-parser-ts graph PATH [--exclude PATTERN ...]
  okf-parser-ts schema PATH [--format json|zod] [--infer-types] [--cast FIELD=TYPE ...]
  okf-parser-ts format PATH [--write] [--exclude PATTERN ...]

Options:
  --exclude PATTERN   Exclude an anchored bundle-relative glob (repeatable)
  --require-spec T    Require a specification document per type, e.g. ".okf/specs/{slug}.md"
  --normative-spec    Report a missing specification document as an error
  --classify          Explain concept/reserved/ignored/invalid bundle membership
  --digests           Include deterministic source/parsed digest rows in inventory
  --infer-types       Infer scalar types from all observed values
  --cast FIELD=TYPE   Apply one strict explicit scalar cast (repeatable)
  --format FORMAT     Schema format: json or zod
  --zod-import MODE   Zod import target: zod or astro
  --write             Rewrite files; without it, format only checks
  --help              Show this help
`;

function writeJson(value: unknown): void {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

async function main(argv: readonly string[]): Promise<number> {
  const parsed = parseArgs({
    args: [...argv],
    allowPositionals: true,
    strict: true,
    options: {
      exclude: { type: "string", multiple: true, default: [] },
      "require-spec": { type: "string" },
      "normative-spec": { type: "boolean", default: false },
      classify: { type: "boolean", default: false },
      digests: { type: "boolean", default: false },
      "infer-types": { type: "boolean", default: false },
      cast: { type: "string", multiple: true, default: [] },
      format: { type: "string", default: "json" },
      "zod-import": { type: "string", default: "zod" },
      write: { type: "boolean", default: false },
      help: { type: "boolean", short: "h", default: false },
    },
  });
  if (parsed.values.help) {
    process.stdout.write(USAGE);
    return 0;
  }
  const [command, root, ...extra] = parsed.positionals;
  if (command === undefined || root === undefined || extra.length > 0) {
    throw new OkfParserError("OKF_CLI_USAGE", USAGE.trimEnd());
  }
  const common = { exclude: parsed.values.exclude };
  if (command === "check") {
    const requireSpec = parsed.values["require-spec"];
    const report = await checkBundle(root, {
      ...common,
      ...(requireSpec === undefined ? {} : { requireSpec }),
      normativeSpec: parsed.values["normative-spec"],
      classify: parsed.values.classify,
    });
    writeJson(report);
    return report.conformant ? 0 : 1;
  }
  if (command === "inventory") {
    writeJson(await inventoryBundle(root, { ...common, digests: parsed.values.digests }));
    return 0;
  }
  if (command === "graph") {
    writeJson(await graphBundle(root, common));
    return 0;
  }
  if (command === "schema") {
    const schemaOptions = {
      ...common,
      inferTypes: parsed.values["infer-types"],
      casts: parsed.values.cast,
    };
    if (parsed.values.format === "zod") {
      const zodImport = parsed.values["zod-import"];
      if (zodImport !== "zod" && zodImport !== "astro") {
        throw new OkfParserError("OKF_CLI_USAGE", "--zod-import must be zod or astro");
      }
      process.stdout.write(await exportZod(root, { ...schemaOptions, zodImport }));
      return 0;
    }
    if (parsed.values.format !== "json") {
      throw new OkfParserError("OKF_CLI_USAGE", "--format must be json or zod");
    }
    writeJson(await exportJsonSchema(root, schemaOptions));
    return 0;
  }
  if (command === "format") {
    const report = await formatPath(root, { ...common, write: parsed.values.write });
    writeJson(report);
    return report.succeeded ? 0 : 1;
  }
  throw new OkfParserError("OKF_CLI_USAGE", `unknown command: ${command}\n\n${USAGE.trimEnd()}`);
}

main(process.argv.slice(2)).then(
  (status) => {
    process.exitCode = status;
  },
  (error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`${message}\n`);
    process.exitCode = 2;
  },
);
