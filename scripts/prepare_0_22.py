"""Temporary deterministic patch application for the stacked 0.22.0 PR."""

from __future__ import annotations

import re
from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def sub(path: str, pattern: str, replacement: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, lambda _match: replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"expected one regex match in {path}, got {count}")
    file.write_text(updated, encoding="utf-8")


# Python service: classification is opt-in and leaves ordinary check untouched.
replace(
    "src/okf_parser/service.py",
    "from okf_parser.bundle import load_bundle, validate_path\n",
    "from okf_parser.bundle import load_bundle, validate_path\nfrom okf_parser.classification import classify_path\n",
)
replace(
    "src/okf_parser/service.py",
    "    *,\n    normative_spec: bool = False,\n) -> dict[str, object]:\n",
    "    *,\n    normative_spec: bool = False,\n    classify: bool = False,\n) -> dict[str, object]:\n",
)
replace(
    "src/okf_parser/service.py",
    '''    return {
        "root": str(report.root),
        "conformant": report.is_conformant,
        "markdown_count": report.markdown_count,
        "concept_count": report.concept_count,
        "reserved_count": report.reserved_count,
        "diagnostics": [item.model_dump(mode="json") for item in report.violations],
    }
''',
    '''    payload: dict[str, object] = {
        "root": str(report.root),
        "conformant": report.is_conformant,
        "markdown_count": report.markdown_count,
        "concept_count": report.concept_count,
        "reserved_count": report.reserved_count,
        "diagnostics": [item.model_dump(mode="json") for item in report.violations],
    }
    if classify:
        payload["classification"] = classify_path(Path(path), exclude)
    return payload
''',
)

# Python CLI and FastMCP keep one shared check service.
replace(
    "src/okf_parser/cli.py",
    '''def check(
    path: str,
    *,
    exclude: RepeatableStrings = None,
    require_spec: str | None = None,
    normative_spec: bool = False,
) -> CliResult[JsonPayload]:
    """Validate every Markdown file recursively as OKF v0.2."""
    payload = check_bundle(path, exclude or (), require_spec, normative_spec=normative_spec)
''',
    '''def check(
    path: str,
    *,
    exclude: RepeatableStrings = None,
    require_spec: str | None = None,
    normative_spec: bool = False,
    classify: bool = False,
) -> CliResult[JsonPayload]:
    """Validate every Markdown file recursively as OKF v0.2."""
    payload = check_bundle(
        path,
        exclude or (),
        require_spec,
        normative_spec=normative_spec,
        classify=classify,
    )
''',
)
replace(
    "src/okf_parser/cli.py",
    '''def mcp_check(
    path: str,
    exclude: RepeatableStrings = None,
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
) -> dict[str, object]:
    """Validate every Markdown file recursively as OKF v0.2."""
    return check_bundle(path, exclude or (), require_spec, normative_spec=normative_spec)
''',
    '''def mcp_check(
    path: str,
    exclude: RepeatableStrings = None,
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
    classify: bool = False,
) -> dict[str, object]:
    """Validate every Markdown file recursively as OKF v0.2."""
    return check_bundle(
        path,
        exclude or (),
        require_spec,
        normative_spec=normative_spec,
        classify=classify,
    )
''',
)

# Python surface regressions.
replace("tests/test_cli.py", "from __future__ import annotations\n\n", "from __future__ import annotations\n\nimport json\n")
replace(
    "tests/test_cli.py",
    "def test_format_write_exits_nonzero_when_a_file_was_skipped(tmp_path: Path) -> None:\n",
    '''def test_check_cli_can_explain_bundle_classification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "concept.md").write_text("---\\ntype: Note\\n---\\n", encoding="utf-8")

    app(["check", str(tmp_path), "--classify"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["classification"] == {
        "concepts": ["concept.md"],
        "reserved": [],
        "ignored": [],
        "invalid_or_untyped": [],
    }


def test_format_write_exits_nonzero_when_a_file_was_skipped(tmp_path: Path) -> None:
''',
)
replace(
    "tests/test_mcp.py",
    '    schema_format = tools["schema"].inputSchema["properties"]["format"]\n',
    '    assert "classify" in tools["check"].inputSchema["properties"]\n\n'
    '    schema_format = tools["schema"].inputSchema["properties"]["format"]\n',
)

# TypeScript report shape.
replace(
    "typescript/src/core.ts",
    '''export interface CheckOptions extends LoadOptions {
  readonly requireSpec?: string;
  readonly normativeSpec?: boolean;
}

export interface CheckReport {
''',
    '''export interface CheckOptions extends LoadOptions {
  readonly requireSpec?: string;
  readonly normativeSpec?: boolean;
  readonly classify?: boolean;
}

export interface BundleClassification {
  readonly concepts: readonly string[];
  readonly reserved: readonly string[];
  readonly ignored: readonly string[];
  readonly invalid_or_untyped: readonly string[];
}

export interface CheckReport {
''',
)
replace(
    "typescript/src/core.ts",
    '''  readonly diagnostics: readonly Diagnostic[];
}

export interface InventoryReport {
''',
    '''  readonly diagnostics: readonly Diagnostic[];
  readonly classification?: BundleClassification;
}

export interface InventoryReport {
''',
)

# Factor discovery so classification can inspect candidates without .okfignore
# while retaining the exact same walk/hard-ignore/symlink behavior.
sub(
    "typescript/src/core.ts",
    r"export async function discoverMarkdown\(.*?\n}\n\nfunction diagnostic\(",
    '''async function discoverMarkdownWithRules(
  root: string,
  rules: ExclusionRules,
  signal?: AbortSignal,
): Promise<readonly string[]> {
  const prunes = !rules.hasNegation;
  const output: string[] = [];

  async function walk(directory: string): Promise<void> {
    signal?.throwIfAborted();
    const entries = await readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => left.name.localeCompare(right.name, "en"));
    for (const entry of entries) {
      signal?.throwIfAborted();
      const candidate = path.join(directory, entry.name);
      const relative = toPosix(path.relative(root, candidate));
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        if (prunes && rules.excludes(relative, { isDir: true })) continue;
        if (!IGNORED_DIRECTORIES.has(entry.name)) await walk(candidate);
      } else if (entry.isFile() && isMarkdownFilename(entry.name)) {
        if (rules.excludes(relative)) continue;
        output.push(candidate);
      }
    }
  }

  const stat = await lstat(root);
  if (!stat.isDirectory()) {
    throw new OkfParserError("OKF_NOT_DIRECTORY", `bundle root is not a directory: ${root}`);
  }
  await walk(root);
  return Object.freeze(output.sort((left, right) => left.localeCompare(right, "en")));
}

export async function discoverMarkdown(
  rootInput: string | URL,
  options: LoadOptions = {},
): Promise<readonly string[]> {
  const root = path.resolve(rootInput instanceof URL ? fileURLToPath(rootInput) : rootInput);
  const rules = await ExclusionRules.read(root, options.exclude ?? []);
  return discoverMarkdownWithRules(root, rules, options.signal);
}

function diagnostic(''',
)

classification_helper = '''async function classifyLoadedBundle(
  bundle: Bundle,
  options: CheckOptions,
): Promise<BundleClassification> {
  const candidates = await discoverMarkdownWithRules(
    bundle.root,
    new ExclusionRules(),
    options.signal,
  );
  const allConcepts = new Set(bundle.concepts.map((concept) => concept.path));
  const concepts = new Set(
    bundle.concepts
      .filter((concept) => concept.conceptType !== "")
      .map((concept) => concept.path),
  );
  const recordedReserved = new Set(bundle.reserved.map((item) => item.path));
  const diagnosticPaths = new Set(bundle.diagnostics.map((item) => item.path));
  const active = new Set([...allConcepts, ...recordedReserved, ...diagnosticPaths]);
  const reserved = new Set(
    [...active].filter((relative) => isReservedDocument(path.join(bundle.root, relative))),
  );
  const candidatePaths = new Set(
    candidates.map((candidate) => toPosix(path.relative(bundle.root, candidate))),
  );
  const sorted = (values: Iterable<string>): readonly string[] =>
    Object.freeze([...values].sort((left, right) => left.localeCompare(right, "en")));

  return Object.freeze({
    concepts: sorted(concepts),
    reserved: sorted(reserved),
    ignored: sorted([...candidatePaths].filter((relative) => !active.has(relative))),
    invalid_or_untyped: sorted(
      [...active].filter((relative) => !concepts.has(relative) && !reserved.has(relative)),
    ),
  });
}

'''
replace(
    "typescript/src/core.ts",
    '''export async function checkBundle(
  root: string | URL,
  options: CheckOptions = {},
): Promise<CheckReport> {
''',
    classification_helper
    + '''export async function checkBundle(
  root: string | URL,
  options: CheckOptions = {},
): Promise<CheckReport> {
''',
)
replace(
    "typescript/src/core.ts",
    '''  return Object.freeze({
    root: bundle.root,
    conformant: !diagnostics.some((item) => item.severity === "error"),
    markdown_count: bundle.markdownCount,
    concept_count: bundle.concepts.length,
    reserved_count: bundle.reserved.length,
    diagnostics: Object.freeze(diagnostics),
  });
}
''',
    '''  const classification =
    options.classify === true ? await classifyLoadedBundle(bundle, options) : undefined;
  return Object.freeze({
    root: bundle.root,
    conformant: !diagnostics.some((item) => item.severity === "error"),
    markdown_count: bundle.markdownCount,
    concept_count: bundle.concepts.length,
    reserved_count: bundle.reserved.length,
    diagnostics: Object.freeze(diagnostics),
    ...(classification === undefined ? {} : { classification }),
  });
}
''',
)

# TypeScript CLI.
replace(
    "typescript/src/cli.ts",
    "okf-parser-ts check PATH [--exclude PATTERN ...] [--require-spec TEMPLATE] [--normative-spec]",
    "okf-parser-ts check PATH [--exclude PATTERN ...] [--require-spec TEMPLATE] [--normative-spec] [--classify]",
)
replace(
    "typescript/src/cli.ts",
    "  --normative-spec    Report a missing specification document as an error\n",
    "  --normative-spec    Report a missing specification document as an error\n"
    "  --classify          Explain concept/reserved/ignored/invalid bundle membership\n",
)
replace(
    "typescript/src/cli.ts",
    '      "normative-spec": { type: "boolean", default: false },\n',
    '      "normative-spec": { type: "boolean", default: false },\n'
    '      classify: { type: "boolean", default: false },\n',
)
replace(
    "typescript/src/cli.ts",
    '      normativeSpec: parsed.values["normative-spec"],\n',
    '      normativeSpec: parsed.values["normative-spec"],\n'
    "      classify: parsed.values.classify,\n",
)

# TypeScript MCP: existing check tool, one boolean input.
replace(
    "typescript/src/mcp.ts",
    '''const normativeSpecSchema = z
  .boolean()
  .optional()
  .describe("Report a missing specification document as an error instead of a warning");
''',
    '''const normativeSpecSchema = z
  .boolean()
  .optional()
  .describe("Report a missing specification document as an error instead of a warning");
const classifySchema = z
  .boolean()
  .optional()
  .describe("Explain concept, reserved, ignored, and invalid bundle membership");
''',
)
replace(
    "typescript/src/mcp.ts",
    "        normative_spec: normativeSpecSchema,\n",
    "        normative_spec: normativeSpecSchema,\n        classify: classifySchema,\n",
)
replace(
    "typescript/src/mcp.ts",
    "    ({ path, exclude, require_spec, normative_spec }) =>\n",
    "    ({ path, exclude, require_spec, normative_spec, classify }) =>\n",
)
replace(
    "typescript/src/mcp.ts",
    "          normativeSpec: normative_spec ?? false,\n",
    "          normativeSpec: normative_spec ?? false,\n          classify: classify ?? false,\n",
)
replace(
    "typescript/test/mcp.test.ts",
    '''  const check = await client.callTool({ name: "check", arguments: { path: root } });
  expect(check.isError).not.toBe(true);
  expect(check.structuredContent).toMatchObject({ conformant: true, concept_count: 1 });
''',
    '''  const check = await client.callTool({
    name: "check",
    arguments: { path: root, classify: true },
  });
  expect(check.isError).not.toBe(true);
  expect(check.structuredContent).toMatchObject({
    conformant: true,
    concept_count: 1,
    classification: {
      concepts: ["sample.md"],
      reserved: [],
      ignored: [],
      invalid_or_untyped: [],
    },
  });
''',
)

# Documentation.
replace(
    "docs/cli.md",
    "uv run okf-parser check path/to/bundle [--exclude PATTERN]... [--require-spec TEMPLATE] [--normative-spec]",
    "uv run okf-parser check path/to/bundle [--exclude PATTERN]... [--require-spec TEMPLATE] [--normative-spec] [--classify]",
)
replace(
    "docs/cli.md",
    '''- `--normative-spec` — promote missing or mismatched required specifications
  from advisory diagnostics to normative errors.

MCP tool: `check`.
''',
    '''- `--normative-spec` — promote missing or mismatched required specifications
  from advisory diagnostics to normative errors.
- `--classify` — add a deterministic `classification` object with `concepts`,
  `reserved`, `ignored`, and `invalid_or_untyped` paths. This explains the
  existing strict check; it does not enable a second strictness mode. Ignored
  paths are those removed by valid `.okfignore` or `--exclude` rules.

MCP tool: `check` with the same optional `classify` argument.
''',
)

# Release metadata.
replace("README.md", "franklinbaldo/okf-parser@v0.21.0", "franklinbaldo/okf-parser@v0.22.0")
replace("pyproject.toml", 'version = "0.21.0"', 'version = "0.22.0"')
replace("typescript/package.json", '"version": "0.21.0"', '"version": "0.22.0"')
replace("typescript-duckdb/package.json", '"version": "0.21.0"', '"version": "0.22.0"')
replace("typescript-duckdb/package.json", '"okf-parser": "^0.21.0"', '"okf-parser": "^0.22.0"')
replace(
    "typescript/src/version.ts",
    'export const PROTOCOL_VERSION = "0.21.0";',
    'export const PROTOCOL_VERSION = "0.22.0";',
)
Path("changelog/0.22.0.md").write_text(
    '''---
type: Release
title: okf-parser 0.22.0
description: make strict bundle membership explainable without adding another validation mode
---

# okf-parser 0.22.0

- `check --classify` adds deterministic `concepts`, `reserved`, `ignored`, and `invalid_or_untyped` path sets while leaving ordinary conformance behavior unchanged (#46).
- The normal `check` path pays no extra traversal cost; unfiltered candidate discovery happens only when classification is explicitly requested.
- Python CLI/service/FastMCP and the TypeScript core/CLI/MCP expose the same opt-in classification semantics.
- Hidden `.claude`/`.agents` paths preserve their literal leading dots under canonical `.okfignore` matching; root-anchored patterns use Git's `/path/` spelling rather than inventing support for the non-matching `./path/` spelling.
- This release does not add a redundant `--strict-coverage` mode: current `check` already rejects in-scope non-reserved Markdown that lacks valid OKF frontmatter/type.
''',
    encoding="utf-8",
)
