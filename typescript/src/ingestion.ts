import path from "node:path";
import { stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import {
  discoverMarkdown,
  isReservedDocument,
  markdownFacts,
  OkfParserError,
  readUtf8Ordered,
  splitOptionalFrontmatter,
} from "./core.js";
import type { FrontmatterValue, LoadOptions, MarkdownFacts } from "./core.js";

export type IngestionCapability = "identity" | "body" | "frontmatter" | "markdown_facts";

export interface DocumentEnvelope {
  readonly ordinal: number;
  readonly path: string;
  readonly size: number | null;
  readonly classification: string;
  readonly body?: string;
  readonly frontmatter?: Readonly<Record<string, FrontmatterValue>> | null;
  readonly facts?: MarkdownFacts;
  readonly error?: string;
}

export interface IngestionOptions extends LoadOptions {
  readonly capabilities?: readonly IngestionCapability[];
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function* ingestDocuments(
  rootInput: string | URL,
  options: IngestionOptions = {},
): AsyncGenerator<DocumentEnvelope> {
  const root = path.resolve(rootInput instanceof URL ? fileURLToPath(rootInput) : rootInput);
  const requested = new Set<IngestionCapability>(options.capabilities ?? ["identity"]);
  requested.add("identity");
  const paths = await discoverMarkdown(rootInput, options);

  if (requested.size === 1) {
    for (const [ordinal, filePath] of paths.entries()) {
      options.signal?.throwIfAborted();
      const relative = path.relative(root, filePath).split(path.sep).join("/");
      let size: number | null = null;
      let error: string | undefined;
      try {
        size = (await stat(filePath)).size;
      } catch (reason) {
        error = messageOf(reason);
      }
      yield Object.freeze({
        ordinal,
        path: relative,
        size,
        classification: error === undefined
          ? isReservedDocument(filePath) ? "reserved" : "candidate"
          : "invalid",
        ...(error === undefined ? {} : { error }),
      });
    }
    return;
  }

  const concurrency = options.readConcurrency ?? 32;
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 256) {
    throw new OkfParserError(
      "OKF_READ_CONCURRENCY",
      "read concurrency must be an integer from 1 through 256",
    );
  }
  let ordinal = 0;
  for await (const loaded of readUtf8Ordered(paths, concurrency, options.signal)) {
    const relative = path.relative(root, loaded.filePath).split(path.sep).join("/");
    if ("error" in loaded) {
      yield Object.freeze({
        ordinal: ordinal++,
        path: relative,
        size: null,
        classification: "invalid",
        error: messageOf(loaded.error),
      });
      continue;
    }
    try {
      const [frontmatter, body] = splitOptionalFrontmatter(loaded.text);
      const reserved = isReservedDocument(loaded.filePath);
      const conceptType = frontmatter?.type;
      const classification = reserved
        ? "reserved"
        : typeof conceptType === "string" && conceptType !== ""
          ? `typed:${conceptType}`
          : "untyped";
      yield Object.freeze({
        ordinal: ordinal++,
        path: relative,
        size: Buffer.byteLength(loaded.text),
        classification,
        ...(requested.has("body") ? { body } : {}),
        ...(requested.has("frontmatter") ? { frontmatter } : {}),
        ...(requested.has("markdown_facts") ? { facts: markdownFacts(body) } : {}),
      });
    } catch (error) {
      yield Object.freeze({
        ordinal: ordinal++,
        path: relative,
        size: Buffer.byteLength(loaded.text),
        classification: "invalid",
        error: messageOf(error),
      });
    }
  }
}
