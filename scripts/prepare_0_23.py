"""Temporary deterministic patch application for the stacked 0.23.0 PR."""

from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


# Python records and parser.
replace(
    "src/okf_parser/models.py",
    "    description: str | None\n    frontmatter_json: str\n    body: str\n",
    "    description: str | None\n    source_digest: str\n    revision_digest: str\n    frontmatter_json: str\n    body: str\n",
)
replace(
    "src/okf_parser/models.py",
    "    path: Path\n    frontmatter: dict[str, YamlValue]\n    body: str\n",
    "    path: Path\n    frontmatter: dict[str, YamlValue]\n    body: str\n    source_digest: str\n    revision_digest: str\n",
)
replace(
    "src/okf_parser/parser.py",
    "from okf_parser.discovery import is_markdown_filename\n",
    "from okf_parser.digests import (\n"
    "    normalize_newlines,\n"
    "    revision_digest as _revision_digest,\n"
    "    source_digest as _source_digest,\n"
    ")\n"
    "from okf_parser.discovery import is_markdown_filename\n",
)
replace(
    "src/okf_parser/parser.py",
    '''def parse_document(path: Path) -> ParsedDocument:\n    """Parse YAML frontmatter and preserve the Markdown body."""\n    try:\n        text = path.read_text(encoding="utf-8")\n    except UnicodeDecodeError as exc:\n        msg = "document must be valid UTF-8"\n        raise DocumentParseError(msg) from exc\n    return parse_document_text(path, text)\n''',
    '''def parse_document(path: Path) -> ParsedDocument:\n    """Parse YAML frontmatter while preserving exact UTF-8 source identity."""\n    try:\n        text = path.read_bytes().decode("utf-8")\n    except UnicodeDecodeError as exc:\n        msg = "document must be valid UTF-8"\n        raise DocumentParseError(msg) from exc\n    return parse_document_text(path, text)\n''',
)
replace(
    "src/okf_parser/parser.py",
    '''    match = _FRONTMATTER_RE.match(text.removeprefix("\\ufeff"))\n    if match is None:\n        msg = "concept must start with YAML frontmatter delimited by ---"\n        raise DocumentParseError(msg)\n\n    return ParsedDocument(\n        path=path,\n        frontmatter=_load_frontmatter(match.group(1)) or {},\n        body=match.group(2) or "",\n    )\n''',
    '''    source_identity = _source_digest(text)\n    normalized = normalize_newlines(text)\n    match = _FRONTMATTER_RE.match(normalized.removeprefix("\\ufeff"))\n    if match is None:\n        msg = "concept must start with YAML frontmatter delimited by ---"\n        raise DocumentParseError(msg)\n\n    frontmatter = _load_frontmatter(match.group(1)) or {}\n    body = match.group(2) or ""\n    return ParsedDocument(\n        path=path,\n        frontmatter=frontmatter,\n        body=body,\n        source_digest=source_identity,\n        revision_digest=_revision_digest(frontmatter, body),\n    )\n''',
)

# Canonical bundle relation exposes both identities.
replace(
    "src/okf_parser/bundle.py",
    '        "description": "string",\n        "frontmatter_json": "string",\n',
    '        "description": "string",\n        "source_digest": "string",\n        "revision_digest": "string",\n        "frontmatter_json": "string",\n',
)
replace(
    "src/okf_parser/bundle.py",
    "        description=parsed.description,\n        frontmatter_json=parsed.frontmatter_json,\n",
    "        description=parsed.description,\n        source_digest=parsed.source_digest,\n        revision_digest=parsed.revision_digest,\n        frontmatter_json=parsed.frontmatter_json,\n",
)

# Existing inventory service/CLI/MCP gains an optional serialized revisions view.
replace(
    "src/okf_parser/service.py",
    "def inventory_bundle(path: str, exclude: Sequence[str] = ()) -> dict[str, object]:\n",
    "def inventory_bundle(\n    path: str, exclude: Sequence[str] = (), *, revisions: bool = False\n) -> dict[str, object]:\n",
)
replace(
    "src/okf_parser/service.py",
    '''    return {"root": str(bundle.root), "types": rows}\n\n\ndef graph_bundle''',
    '''    payload: dict[str, object] = {"root": str(bundle.root), "types": rows}\n    if revisions:\n        payload["revisions"] = (\n            bundle.concepts.select(\n                "concept_id", "path", "source_digest", "revision_digest"\n            )\n            .order_by("path")\n            .execute()\n            .to_dict(orient="records")\n        )\n    return payload\n\n\ndef graph_bundle''',
)
replace(
    "src/okf_parser/cli.py",
    '''@app.command\ndef inventory(path: str, *, exclude: RepeatableStrings = None) -> CliResult[JsonPayload]:\n    """Count concepts by type using an Ibis relation."""\n    return CliResult(inventory_bundle(path, exclude or ()))\n''',
    '''@app.command\ndef inventory(\n    path: str, *, exclude: RepeatableStrings = None, revisions: bool = False\n) -> CliResult[JsonPayload]:\n    """Count concepts by type and optionally expose deterministic revision identity."""\n    return CliResult(inventory_bundle(path, exclude or (), revisions=revisions))\n''',
)
replace(
    "src/okf_parser/cli.py",
    '''def mcp_inventory(path: str, exclude: RepeatableStrings = None) -> dict[str, object]:\n    """Count concepts by type."""\n    return inventory_bundle(path, exclude or ())\n''',
    '''def mcp_inventory(\n    path: str, exclude: RepeatableStrings = None, *, revisions: bool = False\n) -> dict[str, object]:\n    """Count concepts by type and optionally expose deterministic revision identity."""\n    return inventory_bundle(path, exclude or (), revisions=revisions)\n''',
)
replace(
    "tests/test_mcp.py",
    '    assert "classify" in tools["check"].inputSchema["properties"]\n',
    '    assert "classify" in tools["check"].inputSchema["properties"]\n'
    '    assert "revisions" in tools["inventory"].inputSchema["properties"]\n',
)
replace(
    "tests/test_revision_digests.py",
    "from okf_parser.parser import parse_document_text\n",
    "from okf_parser.parser import parse_document_text\nfrom okf_parser.service import inventory_bundle\n",
)
replace(
    "tests/test_revision_digests.py",
    '''def test_bundle_relations_expose_self_describing_digests(tmp_path: Path) -> None:\n''',
    '''def test_inventory_can_expose_serialized_revision_identity(tmp_path: Path) -> None:\n    (tmp_path / "note.md").write_text(\n        "---\\ntype: Note\\n---\\nBody\\n", encoding="utf-8"\n    )\n\n    payload = inventory_bundle(str(tmp_path), revisions=True)\n\n    [revision] = payload["revisions"]\n    assert revision["concept_id"] == "note"\n    assert revision["path"] == "note.md"\n    assert revision["source_digest"].startswith("sha256:")\n    assert revision["revision_digest"].startswith("okf-revision-v1-sha256:")\n\n\ndef test_bundle_relations_expose_self_describing_digests(tmp_path: Path) -> None:\n''',
)

# TypeScript digest implementation and records.
replace(
    "typescript/src/core.ts",
    'import { readFile, readdir, lstat } from "node:fs/promises";\n',
    'import { createHash } from "node:crypto";\n'
    'import { readFile, readdir, lstat } from "node:fs/promises";\n',
)
replace(
    "typescript/src/core.ts",
    '''  readonly frontmatterJson: string;\n  readonly body: string;\n  readonly conceptType: string;\n  readonly title: string | null;\n  readonly description: string | null;\n}\n\nexport interface ConceptRecord {\n''',
    '''  readonly frontmatterJson: string;\n  readonly body: string;\n  readonly conceptType: string;\n  readonly title: string | null;\n  readonly description: string | null;\n  readonly sourceDigest: string;\n  readonly revisionDigest: string;\n}\n\nexport interface ConceptRecord {\n''',
)
replace(
    "typescript/src/core.ts",
    '''  readonly description: string | null;\n  readonly frontmatterJson: string;\n  readonly body: string;\n}\n\nexport interface ReservedRecord {\n''',
    '''  readonly description: string | null;\n  readonly sourceDigest: string;\n  readonly revisionDigest: string;\n  readonly frontmatterJson: string;\n  readonly body: string;\n}\n\nexport interface ReservedRecord {\n''',
)
replace(
    "typescript/src/core.ts",
    '''export interface InventoryReport {\n  readonly root: string;\n  readonly types: readonly {\n    readonly concept_type: string;\n    readonly concept_count: number;\n  }[];\n}\n''',
    '''export interface InventoryOptions extends LoadOptions {\n  readonly revisions?: boolean;\n}\n\nexport interface RevisionRecord {\n  readonly concept_id: string;\n  readonly path: string;\n  readonly source_digest: string;\n  readonly revision_digest: string;\n}\n\nexport interface InventoryReport {\n  readonly root: string;\n  readonly types: readonly {\n    readonly concept_type: string;\n    readonly concept_count: number;\n  }[];\n  readonly revisions?: readonly RevisionRecord[];\n}\n''',
)
replace(
    "typescript/src/core.ts",
    '    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);\n',
    '    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);\n',
)
replace(
    "typescript/src/core.ts",
    '''function optionalText(frontmatter: Readonly<Record<string, FrontmatterValue>>, key: string): string | null {\n''',
    '''function normalizeNewlines(text: string): string {\n  return text.replaceAll("\\r\\n", "\\n").replaceAll("\\r", "\\n");\n}\n\nfunction sha256(text: string): string {\n  return createHash("sha256").update(text, "utf8").digest("hex");\n}\n\nfunction sourceDigest(content: string): string {\n  return `sha256:${sha256(content)}`;\n}\n\nfunction revisionDigest(\n  frontmatter: Readonly<Record<string, FrontmatterValue>>,\n  body: string,\n): string {\n  const canonical = JSON.stringify({\n    body: normalizeNewlines(body),\n    frontmatter: stableFrontmatter(frontmatter),\n    version: 1,\n  });\n  return `okf-revision-v1-sha256:${sha256(canonical)}`;\n}\n\nfunction optionalText(frontmatter: Readonly<Record<string, FrontmatterValue>>, key: string): string | null {\n''',
)
replace(
    "typescript/src/core.ts",
    '''export function parseDocumentContent(content: string, documentPath = "<memory>"): ParsedDocument {\n  const match = FRONTMATTER_RE.exec(content);\n''',
    '''export function parseDocumentContent(content: string, documentPath = "<memory>"): ParsedDocument {\n  const sourceIdentity = sourceDigest(content);\n  const normalized = normalizeNewlines(content);\n  const match = FRONTMATTER_RE.exec(normalized);\n''',
)
replace(
    "typescript/src/core.ts",
    '''    description: optionalText(frontmatter, "description"),\n  });\n}\n''',
    '''    description: optionalText(frontmatter, "description"),\n    sourceDigest: sourceIdentity,\n    revisionDigest: revisionDigest(frontmatter, match[2] ?? ""),\n  });\n}\n''',
)
replace(
    "typescript/src/core.ts",
    '''        description: parsed.description,\n        frontmatterJson: parsed.frontmatterJson,\n''',
    '''        description: parsed.description,\n        sourceDigest: parsed.sourceDigest,\n        revisionDigest: parsed.revisionDigest,\n        frontmatterJson: parsed.frontmatterJson,\n''',
)
replace(
    "typescript/src/core.ts",
    '''export async function inventoryBundle(\n  root: string | URL,\n  options: LoadOptions = {},\n): Promise<InventoryReport> {\n''',
    '''export async function inventoryBundle(\n  root: string | URL,\n  options: InventoryOptions = {},\n): Promise<InventoryReport> {\n''',
)
replace(
    "typescript/src/core.ts",
    '''  return Object.freeze({\n    root: bundle.root,\n    types: Object.freeze(\n      [...counts]\n        .sort(([left], [right]) => left.localeCompare(right, "en"))\n        .map(([concept_type, concept_count]) => Object.freeze({ concept_type, concept_count })),\n    ),\n  });\n}\n''',
    '''  const revisions =\n    options.revisions === true\n      ? Object.freeze(\n          bundle.concepts\n            .map((concept) =>\n              Object.freeze({\n                concept_id: concept.conceptId,\n                path: concept.path,\n                source_digest: concept.sourceDigest,\n                revision_digest: concept.revisionDigest,\n              }),\n            )\n            .sort((left, right) => left.path.localeCompare(right.path, "en")),\n        )\n      : undefined;\n  return Object.freeze({\n    root: bundle.root,\n    types: Object.freeze(\n      [...counts]\n        .sort(([left], [right]) => left.localeCompare(right, "en"))\n        .map(([concept_type, concept_count]) => Object.freeze({ concept_type, concept_count })),\n    ),\n    ...(revisions === undefined ? {} : { revisions }),\n  });\n}\n''',
)

# TypeScript CLI and MCP expose revisions through the existing inventory operation.
replace(
    "typescript/src/cli.ts",
    "  okf-parser-ts inventory PATH [--exclude PATTERN ...]\n",
    "  okf-parser-ts inventory PATH [--exclude PATTERN ...] [--revisions]\n",
)
replace(
    "typescript/src/cli.ts",
    "  --classify          Explain concept/reserved/ignored/invalid bundle membership\n",
    "  --classify          Explain concept/reserved/ignored/invalid bundle membership\n"
    "  --revisions         Include deterministic source/revision identity in inventory\n",
)
replace(
    "typescript/src/cli.ts",
    '      classify: { type: "boolean", default: false },\n',
    '      classify: { type: "boolean", default: false },\n'
    '      revisions: { type: "boolean", default: false },\n',
)
replace(
    "typescript/src/cli.ts",
    '''  if (command === "inventory") {\n    writeJson(await inventoryBundle(root, common));\n''',
    '''  if (command === "inventory") {\n    writeJson(await inventoryBundle(root, { ...common, revisions: parsed.values.revisions }));\n''',
)
replace(
    "typescript/src/mcp.ts",
    '''const classifySchema = z\n  .boolean()\n  .optional()\n  .describe("Explain concept, reserved, ignored, and invalid bundle membership");\n''',
    '''const classifySchema = z\n  .boolean()\n  .optional()\n  .describe("Explain concept, reserved, ignored, and invalid bundle membership");\nconst revisionsSchema = z\n  .boolean()\n  .optional()\n  .describe("Include deterministic source and parsed-revision identity");\n''',
)
replace(
    "typescript/src/mcp.ts",
    '''      inputSchema: z.object({ path: pathSchema, exclude: excludeSchema }),\n    },\n    ({ path, exclude }) => toolResult(() => inventoryBundle(path, loadOptions(exclude))),\n''',
    '''      inputSchema: z.object({\n        path: pathSchema,\n        exclude: excludeSchema,\n        revisions: revisionsSchema,\n      }),\n    },\n    ({ path, exclude, revisions }) =>\n      toolResult(() =>\n        inventoryBundle(path, { ...loadOptions(exclude), revisions: revisions ?? false }),\n      ),\n''',
)
replace(
    "typescript/test/mcp.test.ts",
    '''  const schema = await client.callTool({\n''',
    '''  const inventory = await client.callTool({\n    name: "inventory",\n    arguments: { path: root, revisions: true },\n  });\n  expect(inventory.structuredContent).toMatchObject({\n    revisions: [\n      {\n        concept_id: "sample",\n        path: "sample.md",\n        source_digest: expect.stringMatching(/^sha256:/u),\n        revision_digest: expect.stringMatching(/^okf-revision-v1-sha256:/u),\n      },\n    ],\n  });\n\n  const schema = await client.callTool({\n''',
)

# TypeScript DuckDB adapter persists canonical concept identity fields.
replace(
    "typescript-duckdb/src/index.ts",
    '''      "concept_type VARCHAR NOT NULL, title VARCHAR, description VARCHAR, " +\n      "frontmatter_json VARCHAR NOT NULL, body VARCHAR NOT NULL)",\n''',
    '''      "concept_type VARCHAR NOT NULL, title VARCHAR, description VARCHAR, " +\n      "source_digest VARCHAR NOT NULL, revision_digest VARCHAR NOT NULL, " +\n      "frontmatter_json VARCHAR NOT NULL, body VARCHAR NOT NULL)",\n''',
)
replace(
    "typescript-duckdb/src/index.ts",
    '''      appendNullableVarchar(appender, row.description);\n      appender.appendVarchar(row.frontmatterJson);\n''',
    '''      appendNullableVarchar(appender, row.description);\n      appender.appendVarchar(row.sourceDigest);\n      appender.appendVarchar(row.revisionDigest);\n      appender.appendVarchar(row.frontmatterJson);\n''',
)

# Documentation and release metadata.
replace(
    "docs/cli.md",
    "uv run okf-parser inventory path/to/bundle [--exclude PATTERN]...",
    "uv run okf-parser inventory path/to/bundle [--exclude PATTERN]... [--revisions]",
)
replace(
    "docs/cli.md",
    '''Counts concepts by type using an Ibis relation over the parsed bundle. Always\nexits `0`.\n\nMCP tool: `inventory`.\n''',
    '''Counts concepts by producer-defined `type`. `--revisions` also returns one\ndeterministic identity record per concept with `concept_id`, `path`,\n`source_digest`, and `revision_digest`. `source_digest` hashes exact authored\nUTF-8 source, while `revision_digest` uses the versioned parsed representation\nand normalizes physical newline/frontmatter-order differences. The digest\nprefixes identify their algorithms directly.\n\nMCP tool: `inventory` with the same optional `revisions` argument.\n''',
)
replace("README.md", "franklinbaldo/okf-parser@v0.22.0", "franklinbaldo/okf-parser@v0.23.0")
replace("pyproject.toml", 'version = "0.22.0"', 'version = "0.23.0"')
replace("typescript/package.json", '"version": "0.22.0"', '"version": "0.23.0"')
replace("typescript-duckdb/package.json", '"version": "0.22.0"', '"version": "0.23.0"')
replace("typescript-duckdb/package.json", '"okf-parser": "^0.22.0"', '"okf-parser": "^0.23.0"')
replace(
    "typescript/src/version.ts",
    'export const PROTOCOL_VERSION = "0.22.0";',
    'export const PROTOCOL_VERSION = "0.23.0";',
)
Path("changelog/0.23.0.md").write_text(
    '''---\ntype: Release\ntitle: okf-parser 0.23.0\ndescription: add deterministic source and parsed-revision identity to canonical concepts\n---\n\n# okf-parser 0.23.0\n\n- Every parsed concept exposes a self-describing `sha256:` source digest and `okf-revision-v1-sha256:` parsed revision digest (#53 phase 1).\n- Source identity hashes exact valid UTF-8 source; parsed revision identity canonicalizes frontmatter ordering and normalizes CRLF/bare-CR to LF, so physical rewrites can be distinguished from semantic parsed changes.\n- Python and TypeScript share checked-in digest vectors, including BOM/CRLF and semantic-change cases.\n- The canonical `concepts` relation and both DuckDB adapters persist the digest fields.\n- `inventory --revisions` and the existing inventory MCP tool expose serialized digest records without adding a parallel revisions tool.\n- Git history traversal/indexing remains deliberately deferred to a later #53 slice built on these stable primitives.\n''',
    encoding="utf-8",
)
