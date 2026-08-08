import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "vitest";

import { checkBundle } from "../src/core.js";

async function write(root: string, relative: string, text: string): Promise<void> {
  const destination = path.join(root, relative);
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, text);
}

test("check classification explains strict bundle membership without changing conformance", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "okf-parser-classification-"));
  await write(root, "concept.md", "---\ntype: Note\n---\n# Concept\n");
  await write(root, "untyped.md", "---\ntitle: Untyped\n---\n# Untyped\n");
  await write(root, "broken.md", "# No frontmatter\n");
  await write(root, "docs/index.md", "# Docs\n");
  await write(root, ".claude/skills/hidden.md", "---\ntype: Note\n---\n");
  await write(root, ".agents/skills/hidden.md", "---\ntype: Note\n---\n");
  await write(root, "docs/excluded/hidden.md", "---\ntype: Note\n---\n");
  await write(root, ".okfignore", ".claude/\n.agents/\n/docs/excluded/\n");

  const ordinary = await checkBundle(root);
  const explained = await checkBundle(root, { classify: true });

  expect(ordinary).not.toHaveProperty("classification");
  expect(explained.conformant).toBe(ordinary.conformant);
  expect(explained.diagnostics).toEqual(ordinary.diagnostics);
  expect(explained.classification).toEqual({
    concepts: ["concept.md"],
    reserved: ["docs/index.md"],
    ignored: [
      ".agents/skills/hidden.md",
      ".claude/skills/hidden.md",
      "docs/excluded/hidden.md",
    ],
    invalid_or_untyped: ["broken.md", "untyped.md"],
  });
});
