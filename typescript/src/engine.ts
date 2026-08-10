import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadBundle as loadBundleNative } from "./core.js";
import type { Bundle, LoadOptions } from "./core.js";
import { resolveRustCore, rustLoadBundle } from "./rust-core.js";
import type { EngineMode } from "./rust-core.js";

export interface EngineLoadOptions extends Omit<LoadOptions, "rustCore"> {
  readonly engine?: EngineMode;
  readonly rustCore?: string;
}

export async function loadBundle(
  rootInput: string | URL,
  options: EngineLoadOptions = {},
): Promise<Bundle> {
  const root = path.resolve(rootInput instanceof URL ? fileURLToPath(rootInput) : rootInput);
  const executable = resolveRustCore({ engine: options.engine, explicit: options.rustCore });
  if (executable === undefined) {
    const { engine: _engine, rustCore: _rustCore, ...nativeOptions } = options;
    return loadBundleNative(root, nativeOptions);
  }
  return rustLoadBundle(root, executable, {
    readConcurrency: options.readConcurrency ?? 32,
    ...(options.exclude === undefined ? {} : { exclude: options.exclude }),
    ...(options.signal === undefined ? {} : { signal: options.signal }),
  });
}
