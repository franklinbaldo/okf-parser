import { mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { describe, expect, test } from "vitest";

import { resolveRustCore } from "../src/rust-core.js";

describe("Rust engine resolution", () => {
  test("native mode disables explicit and ambient Rust", () => {
    expect(
      resolveRustCore({
        engine: "native",
        explicit: "/explicit/core",
        environment: { OKF_CORE: "/env/core", PATH: "/path" },
      }),
    ).toBeUndefined();
  });

  test("explicit path wins over environment", () => {
    expect(
      resolveRustCore({
        explicit: "/explicit/core",
        environment: { OKF_CORE: "/env/core", PATH: "" },
      }),
    ).toBe("/explicit/core");
  });

  test("environment and PATH resolution", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "okf-core-path-"));
    const binary = path.join(
      directory,
      process.platform === "win32" ? "okf-core.exe" : "okf-core",
    );
    await writeFile(binary, "fixture");
    expect(resolveRustCore({ environment: { OKF_CORE: "/env/core", PATH: directory } })).toBe(
      "/env/core",
    );
    expect(resolveRustCore({ environment: { PATH: directory } })).toBe(binary);
    expect(resolveRustCore({ environment: { PATH: "" } })).toBeUndefined();
  });
});
