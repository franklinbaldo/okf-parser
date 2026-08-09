import { spawn } from "node:child_process";

import type { MarkdownFacts } from "./core.js";

export class RustCoreError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RustCoreError";
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
