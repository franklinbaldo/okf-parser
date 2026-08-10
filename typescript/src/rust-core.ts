import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import type { Bundle, Diagnostic, MarkdownFacts } from "./core.js";

export type EngineMode = "auto" | "native";

export class RustCoreError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RustCoreError";
  }
}

function binaryName(): string {
  return process.platform === "win32" ? "okf-core.exe" : "okf-core";
}

export function packagedRustCore(): string | undefined {
  const candidate = fileURLToPath(new URL(`../native/${binaryName()}`, import.meta.url));
  return existsSync(candidate) ? candidate : undefined;
}

function pathRustCore(environment: NodeJS.ProcessEnv): string | undefined {
  for (const directory of (environment.PATH ?? "").split(path.delimiter)) {
    if (directory === "") continue;
    const candidate = path.join(directory, binaryName());
    if (existsSync(candidate)) return candidate;
  }
  return undefined;
}

export function resolveRustCore(
  options: {
    readonly engine?: EngineMode;
    readonly explicit?: string;
    readonly environment?: NodeJS.ProcessEnv;
  } = {},
): string | undefined {
  const engine = options.engine ?? "auto";
  if (engine === "native") return undefined;
  if (engine !== "auto") throw new TypeError(`unsupported OKF engine mode: ${String(engine)}`);
  if (options.explicit !== undefined) return options.explicit;
  const packaged = packagedRustCore();
  if (packaged !== undefined) return packaged;
  const environment = options.environment ?? process.env;
  if (environment.OKF_CORE !== undefined && environment.OKF_CORE !== "") return environment.OKF_CORE;
  return pathRustCore(environment);
}

interface RustBundle {
  readonly root: string;
  readonly concepts: readonly Record<string, unknown>[];
  readonly reserved: readonly Record<string, unknown>[];
  readonly links: readonly Record<string, unknown>[];
  readonly diagnostics: readonly Diagnostic[];
  readonly markdown_count: number;
}

export async function rustLoadBundle(
  root: string,
  executable: string,
  options: {
    readonly exclude?: readonly string[];
    readonly readConcurrency: number;
    readonly signal?: AbortSignal;
  },
): Promise<Bundle> {
  options.signal?.throwIfAborted();
  const args = ["load", root, "--read-concurrency", String(options.readConcurrency)];
  for (const pattern of options.exclude ?? []) args.push("--exclude", pattern);
  const child = spawn(executable, args, { stdio: ["ignore", "pipe", "pipe"] });
  const stdout: Buffer[] = [];
  const stderr: Buffer[] = [];
  child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
  child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk));
  const abort = (): void => {
    child.kill();
  };
  options.signal?.addEventListener("abort", abort, { once: true });
  const code = await new Promise<number | null>((resolve, reject) => {
    child.once("error", reject);
    child.once("close", resolve);
  }).finally(() => options.signal?.removeEventListener("abort", abort));
  options.signal?.throwIfAborted();
  if (code !== 0) {
    throw new RustCoreError(
      Buffer.concat(stderr).toString("utf8").trim() || `okf exited with ${String(code)}`,
    );
  }
  try {
    const value = JSON.parse(Buffer.concat(stdout).toString("utf8")) as RustBundle;
    return Object.freeze({
      root: value.root,
      concepts: Object.freeze(
        value.concepts.map((item) =>
          Object.freeze({
            conceptId: item.concept_id,
            logicalKey: item.logical_key,
            path: item.path,
            conceptType: item.concept_type,
            title: item.title,
            description: item.description,
            sourceDigest: item.source_digest,
            parsedDigest: item.parsed_digest,
            frontmatterJson: item.frontmatter_json,
            body: item.body,
          }),
        ),
      ) as Bundle["concepts"],
      reserved: Object.freeze(
        value.reserved.map((item) =>
          Object.freeze({
            path: item.path,
            filename: item.filename,
            body: item.body,
          }),
        ),
      ) as Bundle["reserved"],
      links: Object.freeze(
        value.links.map((item) =>
          Object.freeze({
            sourceId: item.source_id,
            rawTarget: item.raw_target,
            targetId: item.target_id,
            exists: item.exists,
            origin: item.origin,
          }),
        ),
      ) as Bundle["links"],
      diagnostics: Object.freeze(value.diagnostics),
      markdownCount: value.markdown_count,
    });
  } catch (error) {
    throw new RustCoreError(
      `invalid okf load response: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

export async function rustMarkdownFactsBatch(
  bodies: readonly string[],
  executable: string,
  signal?: AbortSignal,
): Promise<readonly MarkdownFacts[]> {
  signal?.throwIfAborted();
  const child = spawn(executable, [], { stdio: ["pipe", "pipe", "pipe"] });
  const stdout: Buffer[] = [];
  const stderr: Buffer[] = [];
  child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
  child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk));
  const abort = (): void => {
    child.kill();
  };
  signal?.addEventListener("abort", abort, { once: true });
  child.stdin.end(JSON.stringify({ documents: bodies }));

  const code = await new Promise<number | null>((resolve, reject) => {
    child.once("error", reject);
    child.once("close", resolve);
  }).finally(() => signal?.removeEventListener("abort", abort));
  signal?.throwIfAborted();
  if (code !== 0) {
    throw new RustCoreError(
      Buffer.concat(stderr).toString("utf8").trim() || `okf-core exited with ${String(code)}`,
    );
  }
  try {
    const payload: unknown = JSON.parse(Buffer.concat(stdout).toString("utf8"));
    if (!Array.isArray(payload) || payload.length !== bodies.length) {
      throw new Error("response cardinality does not match request");
    }
    return payload as readonly MarkdownFacts[];
  } catch (error) {
    throw new RustCoreError(
      `invalid okf-core response: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
