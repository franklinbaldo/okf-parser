from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace(path: str, old: str, new: str, *, count: int = -1) -> None:
    text = read(path)
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:80]!r}")
    write(path, text.replace(old, new, count))


DIGESTS_PY = '''"""Deterministic physical-source and parsed-content identity for OKF concepts."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from okf_parser.models import YamlValue

_SOURCE_PREFIX = "sha256:"
_PARSED_PREFIX = "okf-parsed-v1-jcs-sha256:"


def normalize_newlines(text: str) -> str:
    """Normalize CRLF and bare CR to LF for the parsed representation."""
    return text.replace("\\r\\n", "\\n").replace("\\r", "\\n")


def _reject_lone_surrogates(value: str) -> None:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        msg = "JCS strings must not contain lone UTF-16 surrogate code points"
        raise ValueError(msg)


def _jcs_string(value: str) -> str:
    _reject_lone_surrogates(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utf16_sort_key(value: str) -> bytes:
    _reject_lone_surrogates(value)
    return value.encode("utf-16-be")


def canonical_json(value: YamlValue) -> str:
    """Serialize the OKF frontmatter value subset using RFC 8785/JCS rules.

    OKF failsafe frontmatter preserves ordinary scalars as strings, so this
    digest domain only needs null, strings, arrays and objects. Object keys are
    ordered by unsigned UTF-16 code units as required by JCS; no locale or
    host-language object enumeration order participates in the result.
    """
    if value is None:
        return "null"
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    items = (
        f"{_jcs_string(key)}:{canonical_json(value[key])}"
        for key in sorted(value, key=_utf16_sort_key)
    )
    return "{" + ",".join(items) + "}"


def source_digest(text: str) -> str:
    """Hash the exact valid UTF-8 text supplied to the parser."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{_SOURCE_PREFIX}{digest}"


def parsed_digest(frontmatter: dict[str, YamlValue], body: str) -> str:
    """Hash the versioned parsed OKF value without claiming Revision identity."""
    payload: YamlValue = [frontmatter, normalize_newlines(body)]
    canonical = canonical_json(payload)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_PARSED_PREFIX}{digest}"
'''
write("src/okf_parser/digests.py", DIGESTS_PY)

# The field rename is intentionally repo-wide within product/tests/docs: the old
# name is unpublished and incorrectly conflates a parsed-value fingerprint with
# the historical Revision entity planned by #53.
for relative in [
    "src/okf_parser/models.py",
    "src/okf_parser/parser.py",
    "src/okf_parser/bundle.py",
    "src/okf_parser/service.py",
    "tests/test_mcp.py",
    "typescript/src/core.ts",
    "typescript/src/mcp.ts",
    "typescript-duckdb/src/index.ts",
    "typescript-duckdb/test/revisions.test.ts",
    "typescript/test/mcp.test.ts",
    "typescript/test/revision-digests.test.ts",
]:
    text = read(relative)
    text = text.replace("revision_digest", "parsed_digest")
    text = text.replace("revisionDigest", "parsedDigest")
    write(relative, text)

# Python parser imports/calls follow the renamed primitive after the mechanical pass.
replace(
    "src/okf_parser/parser.py",
    "parsed_digest as _parsed_digest",
    "parsed_digest as _parsed_digest",
)

# Replace the TypeScript digest implementation with an RFC 8785/JCS subset encoder.
ts_core = read("typescript/src/core.ts")
old = '''function normalizeNewlines(text: string): string {
  return text.replaceAll("\\r\\n", "\\n").replaceAll("\\r", "\\n");
}

function sha256(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

function sourceDigest(content: string): string {
  return `sha256:${sha256(content)}`;
}

function parsedDigest(
  frontmatter: Readonly<Record<string, FrontmatterValue>>,
  body: string,
): string {
  const canonical = JSON.stringify({
    body: normalizeNewlines(body),
    frontmatter: stableFrontmatter(frontmatter),
    version: 1,
  });
  return `okf-revision-v1-sha256:${sha256(canonical)}`;
}
'''
new = '''function normalizeNewlines(text: string): string {
  return text.replaceAll("\\r\\n", "\\n").replaceAll("\\r", "\\n");
}

function sha256(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

function sourceDigest(content: string): string {
  return `sha256:${sha256(content)}`;
}

function assertJcsString(value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) {
        throw new DocumentParseError("JCS strings must not contain lone UTF-16 surrogates");
      }
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      throw new DocumentParseError("JCS strings must not contain lone UTF-16 surrogates");
    }
  }
}

function jcsString(value: string): string {
  assertJcsString(value);
  return JSON.stringify(value);
}

function canonicalJson(value: FrontmatterValue): string {
  if (value === null) return "null";
  if (typeof value === "string") return jcsString(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const properties = Object.keys(value).sort();
  return `{${properties
    .map((property) => `${jcsString(property)}:${canonicalJson(value[property] ?? null)}`)
    .join(",")}}`;
}

function parsedDigest(
  frontmatter: Readonly<Record<string, FrontmatterValue>>,
  body: string,
): string {
  const payload: FrontmatterValue = [frontmatter, normalizeNewlines(body)];
  return `okf-parsed-v1-jcs-sha256:${sha256(canonicalJson(payload))}`;
}
'''
if old not in ts_core:
    raise SystemExit("TypeScript digest implementation block not found")
write("typescript/src/core.ts", ts_core.replace(old, new, 1))

# Inventory exposes content digests, not historical Revision entities.
replace(
    "src/okf_parser/service.py",
    '''def inventory_bundle(
    path: str, exclude: Sequence[str] = (), *, revisions: bool = False
) -> dict[str, object]:
''',
    '''def inventory_bundle(
    path: str, exclude: Sequence[str] = (), *, digests: bool = False
) -> dict[str, object]:
''',
)
replace("src/okf_parser/service.py", "    if revisions:\n", "    if digests:\n")
replace("src/okf_parser/service.py", 'payload["revisions"]', 'payload["digests"]')

replace(
    "src/okf_parser/cli.py",
    '''def inventory(
    path: str, *, exclude: RepeatableStrings = None, revisions: bool = False
) -> CliResult[JsonPayload]:
    """Count concepts by type and optionally expose deterministic revision identity."""
    return CliResult(inventory_bundle(path, exclude or (), revisions=revisions))
''',
    '''def inventory(
    path: str, *, exclude: RepeatableStrings = None, digests: bool = False
) -> CliResult[JsonPayload]:
    """Count concepts by type and optionally expose deterministic content digests."""
    return CliResult(inventory_bundle(path, exclude or (), digests=digests))
''',
)
replace(
    "src/okf_parser/cli.py",
    '''def mcp_inventory(
    path: str, exclude: RepeatableStrings = None, *, revisions: bool = False
) -> dict[str, object]:
    """Count concepts by type and optionally expose deterministic revision identity."""
    return inventory_bundle(path, exclude or (), revisions=revisions)
''',
    '''def mcp_inventory(
    path: str, exclude: RepeatableStrings = None, *, digests: bool = False
) -> dict[str, object]:
    """Count concepts by type and optionally expose deterministic content digests."""
    return inventory_bundle(path, exclude or (), digests=digests)
''',
)

# TypeScript public inventory surface: --digests / {digests}.
ts_core = read("typescript/src/core.ts")
ts_core = ts_core.replace("readonly revisions?: boolean;", "readonly digests?: boolean;")
ts_core = ts_core.replace("export interface RevisionRecord", "export interface DigestRecord")
ts_core = ts_core.replace("readonly revisions?: readonly RevisionRecord[];", "readonly digests?: readonly DigestRecord[];")
ts_core = ts_core.replace("const revisions =\n    options.revisions === true", "const digests =\n    options.digests === true")
ts_core = ts_core.replace("...(revisions === undefined ? {} : { revisions }),", "...(digests === undefined ? {} : { digests }),")
write("typescript/src/core.ts", ts_core)

for relative in ["typescript/src/cli.ts", "typescript/src/mcp.ts", "typescript/test/mcp.test.ts"]:
    text = read(relative)
    text = text.replace("--revisions", "--digests")
    text = text.replace("revisions:", "digests:")
    text = text.replace("values.revisions", "values.digests")
    text = text.replace('revisions: { type: "boolean", default: false }', 'digests: { type: "boolean", default: false }')
    text = text.replace("source/revision identity", "source/parsed content digests")
    write(relative, text)

# Rename the conformance/test fixtures before rewriting their contents.
renames = {
    "conformance/revision-digests.json": "conformance/content-digests.json",
    "tests/test_revision_digests.py": "tests/test_content_digests.py",
    "typescript/test/revision-digests.test.ts": "typescript/test/content-digests.test.ts",
    "typescript-duckdb/test/revisions.test.ts": "typescript-duckdb/test/digests.test.ts",
}
for old_name, new_name in renames.items():
    old_path = ROOT / old_name
    new_path = ROOT / new_name
    if not old_path.exists():
        raise SystemExit(f"missing rename source: {old_name}")
    old_path.rename(new_path)

# Cross-language vectors deliberately cover the two reviewer counterexamples,
# recursive ordering, and a non-BMP key where Python code-point ordering differs
# from JCS UTF-16 code-unit ordering.
def reject_surrogates(value: str) -> None:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("lone surrogate")


def jcs_string(value: str) -> str:
    reject_surrogates(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sort_key(value: str) -> bytes:
    reject_surrogates(value)
    return value.encode("utf-16-be")


def jcs(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return jcs_string(value)
    if isinstance(value, list):
        return "[" + ",".join(jcs(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{jcs_string(key)}:{jcs(value[key])}" for key in sorted(value, key=sort_key)
        ) + "}"
    raise TypeError(type(value))


def digest_source(source: str) -> str:
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def digest_parsed(frontmatter: dict[str, object], body: str) -> tuple[str, str]:
    canonical = jcs([frontmatter, body.replace("\r\n", "\n").replace("\r", "\n")])
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return canonical, "okf-parsed-v1-jcs-sha256:" + digest


cases = [
    ("canonical", "---\ntype: Note\ntitle: Olá\n---\nBody\n", {"type": "Note", "title": "Olá"}, "Body\n"),
    ("bom_crlf", "\ufeff---\r\ntype: Note\r\ntitle: Olá\r\n---\r\nBody\r\n", {"type": "Note", "title": "Olá"}, "Body\n"),
    ("semantic_change", "---\ntype: Note\ntitle: Olá\n---\nChanged\n", {"type": "Note", "title": "Olá"}, "Changed\n"),
    ("unicode_keys", "---\ntype: Note\nz: one\nä: two\n---\nBody\n", {"type": "Note", "z": "one", "ä": "two"}, "Body\n"),
    ("integer_like_keys", '---\ntype: Note\n"10": a\n"2": b\n---\nBody\n', {"type": "Note", "10": "a", "2": "b"}, "Body\n"),
    ("nested_keys", '---\ntype: Note\nnested:\n  "10": ten\n  "2": two\n  z: zee\n  ä: umlaut\n---\nBody\n', {"type": "Note", "nested": {"10": "ten", "2": "two", "z": "zee", "ä": "umlaut"}}, "Body\n"),
    ("utf16_order", '---\ntype: Note\n"😀": emoji\n"דּ": hebrew\n---\nBody\n', {"type": "Note", "😀": "emoji", "דּ": "hebrew"}, "Body\n"),
]
vector_cases = []
for name, source, frontmatter, body in cases:
    canonical, parsed = digest_parsed(frontmatter, body)
    vector_cases.append(
        {
            "name": name,
            "source": source,
            "source_digest": digest_source(source),
            "parsed_canonical": canonical,
            "parsed_digest": parsed,
        }
    )
write(
    "conformance/content-digests.json",
    json.dumps(
        {"algorithm": "okf-parsed-v1-jcs-sha256", "cases": vector_cases},
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
)

PY_TEST = '''"""Cross-language deterministic source and parsed-content identity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from okf_parser import load_bundle
from okf_parser.digests import canonical_json
from okf_parser.parser import parse_document_text


def _vectors() -> list[dict[str, str]]:
    path = Path(__file__).parents[1] / "conformance" / "content-digests.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return cast("list[dict[str, str]]", payload["cases"])


def test_content_digest_vectors_are_pinned() -> None:
    for case in _vectors():
        parsed = parse_document_text(Path(f"{case['name']}.md"), case["source"])
        assert parsed.source_digest == case["source_digest"]
        assert parsed.parsed_digest == case["parsed_digest"]
        assert canonical_json([parsed.frontmatter, parsed.body]) == case["parsed_canonical"]


def test_physical_variant_changes_source_not_parsed_value() -> None:
    canonical, physical = _vectors()[:2]
    assert canonical["source_digest"] != physical["source_digest"]
    assert canonical["parsed_digest"] == physical["parsed_digest"]


def test_semantic_body_change_changes_parsed_value() -> None:
    canonical, _, semantic = _vectors()[:3]
    assert canonical["parsed_digest"] != semantic["parsed_digest"]


def test_bundle_relations_expose_self_describing_digests(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text(
        "---\\ntype: Note\\ntitle: Olá\\n---\\nBody\\n",
        encoding="utf-8",
    )
    bundle = load_bundle(tmp_path)
    [row] = bundle.concepts.select("source_digest", "parsed_digest").execute().to_dict(
        orient="records"
    )
    assert row["source_digest"].startswith("sha256:")
    assert row["parsed_digest"].startswith("okf-parsed-v1-jcs-sha256:")
'''
write("tests/test_content_digests.py", PY_TEST)

TS_TEST = '''import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

import { parseDocumentContent } from "../src/core.js";

interface DigestCase {
  readonly name: string;
  readonly source: string;
  readonly source_digest: string;
  readonly parsed_digest: string;
}

async function vectors(): Promise<readonly DigestCase[]> {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const text = await readFile(path.resolve(here, "../../conformance/content-digests.json"), "utf8");
  return (JSON.parse(text) as { readonly cases: readonly DigestCase[] }).cases;
}

describe("content digest conformance", () => {
  test("matches shared source and parsed digest vectors", async () => {
    for (const item of await vectors()) {
      const parsed = parseDocumentContent(item.source, `${item.name}.md`);
      expect(parsed.sourceDigest).toBe(item.source_digest);
      expect(parsed.parsedDigest).toBe(item.parsed_digest);
    }
  });

  test("physical BOM/CRLF differences preserve parsed identity", async () => {
    const [canonical, physical] = await vectors();
    expect(canonical?.source_digest).not.toBe(physical?.source_digest);
    expect(canonical?.parsed_digest).toBe(physical?.parsed_digest);
  });
});
'''
write("typescript/test/content-digests.test.ts", TS_TEST)

# Update the DuckDB regression filename/content after the mechanical field rename.
duck_test = read("typescript-duckdb/test/digests.test.ts")
duck_test = duck_test.replace("revision digests", "content digests")
duck_test = duck_test.replace("revision identity", "parsed content identity")
write("typescript-duckdb/test/digests.test.ts", duck_test)

# MCP tests use the new serialized field and option name.
for relative in ["tests/test_mcp.py", "typescript/test/mcp.test.ts"]:
    text = read(relative)
    text = text.replace("revisions", "digests")
    write(relative, text)

# Human-facing CLI contract.
replace(
    "docs/cli.md",
    '''uv run okf-parser inventory path/to/bundle [--exclude PATTERN]... [--revisions]
```

Counts concepts by producer-defined `type`. `--revisions` also returns one
deterministic identity record per concept with `concept_id`, `path`,
`source_digest`, and `revision_digest`. `source_digest` hashes exact authored
UTF-8 source, while `revision_digest` uses the versioned parsed representation
and normalizes physical newline/frontmatter-order differences. The digest
prefixes identify their algorithms directly.

MCP tool: `inventory` with the same optional `revisions` argument.
''',
    '''uv run okf-parser inventory path/to/bundle [--exclude PATTERN]... [--digests]
```

Counts concepts by producer-defined `type`. `--digests` also returns one record
per concept with `concept_id`, `path`, `source_digest`, and `parsed_digest`.
`source_digest` hashes the exact valid UTF-8 source. `parsed_digest` fingerprints
the parser's frontmatter/body value using the RFC 8785/JCS string/object rules
and LF-normalized parsed body under the self-describing
`okf-parsed-v1-jcs-sha256:` prefix. It is not a historical `Revision` id and it
does not claim editorial/Markdown semantic equivalence.

MCP tool: `inventory` with the same optional `digests` argument.
''',
)

# TypeScript CLI prose/option spelling.
ts_cli = read("typescript/src/cli.ts")
ts_cli = ts_cli.replace("[--revisions]", "[--digests]")
ts_cli = ts_cli.replace("--revisions         Include deterministic source/revision identity in inventory", "--digests           Include deterministic source/parsed content digests in inventory")
ts_cli = ts_cli.replace('revisions: { type: "boolean", default: false }', 'digests: { type: "boolean", default: false }')
ts_cli = ts_cli.replace("revisions: parsed.values.revisions", "digests: parsed.values.digests")
write("typescript/src/cli.ts", ts_cli)

# Changelog records the corrected model before 0.23.0 is published.
write(
    "changelog/0.23.0.md",
    '''---
type: Release
title: okf-parser 0.23.0
description: add deterministic source and parsed-content digests to canonical concepts
---

# okf-parser 0.23.0

- Every parsed concept exposes `sha256:` exact-source identity and an `okf-parsed-v1-jcs-sha256:` fingerprint of the parsed frontmatter/body value (#53 phase 1).
- `source_digest` identifies exact valid UTF-8 source; `parsed_digest` intentionally does not identify the historical `Revision` entity and does not claim Markdown/editorial semantic equivalence.
- The parsed digest uses the RFC 8785/JCS ordering and string rules over OKF's null/string/array/object frontmatter subset, with LF-normalized parsed body.
- Shared Python/TypeScript vectors cover locale-sensitive keys, integer-like keys, nested maps and non-BMP UTF-16 ordering so host-language object enumeration cannot affect the digest.
- Canonical `concepts` relations and both DuckDB adapters persist `source_digest` and `parsed_digest`.
- `inventory --digests` and the existing inventory MCP tool expose serialized digest records without adding a parallel tool.
- Historical Revision identity and optional Git provenance remain deliberately deferred to a later #53 slice built on these primitives.
''',
)

# README release reference remains synchronized with 0.23.0; only wording related
# to the new digest API is changed if present.
readme = read("README.md")
readme = readme.replace("revision_digest", "parsed_digest")
readme = readme.replace("--revisions", "--digests")
write("README.md", readme)

# No unpublished old public names may survive in product code/docs for this PR.
for root in ["src", "tests", "typescript/src", "typescript/test", "typescript-duckdb/src", "typescript-duckdb/test", "docs/cli.md", "changelog/0.23.0.md"]:
    path = ROOT / root
    files = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
    for item in files:
        text = item.read_text(encoding="utf-8")
        if "revision_digest" in text or "revisionDigest" in text or "--revisions" in text:
            raise SystemExit(f"stale unpublished revision-digest surface in {item.relative_to(ROOT)}")
