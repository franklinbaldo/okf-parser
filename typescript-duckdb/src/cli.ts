#!/usr/bin/env node

import { exportDuckDb } from "./index.js";

interface CliOptions {
  readonly root: string;
  readonly database: string;
  readonly schema: string;
  readonly overwrite: boolean;
  readonly exclude: readonly string[];
}

function usage(): string {
  return [
    "Usage: okf-parser-ts-duckdb PATH [options]",
    "",
    "Options:",
    "  --database FILE   DuckDB file to create or update (default: okf.duckdb)",
    "  --schema NAME     Target schema (default: okf)",
    "  --overwrite       Replace existing OKF tables",
    "  --exclude GLOB    Exclude an anchored bundle-relative path; repeatable",
    "  --help            Show this help",
  ].join("\n");
}

function parseArgs(argv: readonly string[]): CliOptions | null {
  if (argv.includes("--help")) return null;
  const positional: string[] = [];
  const exclude: string[] = [];
  let database = "okf.duckdb";
  let schema = "okf";
  let overwrite = false;

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--overwrite") {
      overwrite = true;
      continue;
    }
    if (argument === "--database" || argument === "--schema" || argument === "--exclude") {
      const value = argv[index + 1];
      if (value === undefined) throw new TypeError(`${argument} requires a value`);
      index += 1;
      if (argument === "--database") database = value;
      else if (argument === "--schema") schema = value;
      else exclude.push(value);
      continue;
    }
    if (argument?.startsWith("--")) throw new TypeError(`unknown option: ${argument}`);
    if (argument !== undefined) positional.push(argument);
  }

  if (positional.length !== 1) throw new TypeError("exactly one bundle PATH is required");
  return { root: positional[0] ?? "", database, schema, overwrite, exclude };
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  if (options === null) {
    process.stdout.write(`${usage()}\n`);
    return;
  }
  const report = await exportDuckDb(options.root, {
    database: options.database,
    schema: options.schema,
    overwrite: options.overwrite,
    exclude: options.exclude,
  });
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
