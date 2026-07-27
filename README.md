---
type: Project
title: okf-parser
description: Relational inspection and validation for Open Knowledge Format bundles
---

# okf-parser

Relational inspection and validation for
[Open Knowledge Format (OKF) v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundles.

`okf-parser` reads an OKF bundle without imposing a domain taxonomy, preserves
unknown frontmatter fields, and exposes concepts and links as
[Ibis](https://ibis-project.org/) tables. This makes bundle-wide rules—identity,
lineage, cardinality, provenance, and profile-specific constraints—expressible
as deterministic relational checks.

## Why another OKF tool?

The ecosystem already has good static linters and generators, including
`okflint`, `okf-cli`, and `google-okf`. This project focuses on a different
layer:

- compile a bundle into queryable relational tables;
- project those same relations into a NetworkX graph;
- validate OKF v0.2 conformance without rejecting extensions;
- distinguish normative errors from advisory diagnostics;
- let projects add cross-concept rules as Ibis expressions;
- produce stable human-readable and JSON reports for CI and agents.

The parser and validation model are inspired by
[`franklinbaldo/sisprev`](https://github.com/franklinbaldo/sisprev): parse
documents independently from semantic validation, aggregate violations instead
of failing at the first bad concept, preserve authored bodies, and test
filesystem identity explicitly. No Sisprev-specific legal types are copied into
the core.

[`mrorigo/rust-okf`](https://github.com/mrorigo/rust-okf) inspired the stable
logical key, conservative metadata preservation, BOM/CRLF handling, and clean
separation between bundle parsing and downstream query surfaces. Its BM25,
vector index, storage format, and HTTP server are intentionally outside this
project's scope.

## Quick start

```bash
uv sync
uv run okf-parser check path/to/bundle
uv run okf-parser inventory path/to/bundle
uv run okf-parser graph path/to/bundle
uv run okf-parser format path/to/bundle
uv run okf-parser format path/to/bundle --write
uv run okf-parser duckdb path/to/bundle knowledge.duckdb
uv run okf-parser duckdb path/to/bundle knowledge.duckdb --overwrite
```

The command exits with status `1` only when normative errors exist. Broken
cross-links are warnings because OKF v0.2 explicitly says they do not make a
bundle non-conformant.

`format` numbers ordered lists consecutively (`1. 2. 3.`) without padding them
to an even width, so appending an item does not rewrite the lines above it,
including lists nested inside blockquotes or other lists. A
list whose markers would exceed CommonMark's nine-digit limit keeps plain
numbering instead. Formatting never rewrites a file into a different document:
a file whose block structure would change is reported in `skipped_paths` and
left on disk.

## GitHub Actions

Add the repository as a CI check:

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: franklinbaldo/okf-parser@v1
    with:
      path: knowledge
```

The composite action installs a pinned uv version and executes the same
`validate_path()` function used by the Python API, CLI, and MCP server.

Releases are published to PyPI from GitHub Releases through OIDC Trusted
Publishing. No long-lived PyPI token is stored in the repository.

Every pull request must increase the SemVer version in `pyproject.toml` and add
exactly one matching `changelog/<version>.md` entry. CI compares both against
the target branch before allowing merge.

## MCP

```bash
uv run okf-parser serve
uv run okf-parser-mcp
uv run fastmcp run
```

Read-only tools: `check`, `inventory`, `graph`, and `format_check`.

## DuckDB

`okf-parser` is a regular uv-managed Python app; the DuckDB integration is part
of the same package, not a native C++ subproject:

```python
import duckdb

from okf_parser.duckdb import attach_okf

connection = duckdb.connect("knowledge.duckdb")
attach_okf(connection, "knowledge/")

connection.sql("""
    SELECT concept_type, count(*)
    FROM okf.concepts
    GROUP BY concept_type
""").show()
```

The call creates `okf.concepts`, `okf.links`, `okf.reserved`, and
`okf.diagnostics` as ordinary DuckDB tables. Materializing twice into the same
schema raises `BundleExportError` rather than clobbering an earlier export; pass
`overwrite=True` (or `--overwrite` on the command line) to replace the four
tables.

## Python API

```python
from pathlib import Path

from okf_parser import load_bundle, validate_path

bundle = load_bundle(Path("knowledge"))
print(bundle.concepts.execute())
print(bundle.links.execute())
print(bundle.to_networkx())
print(bundle.validate())

report = validate_path(Path("knowledge"))
assert report.markdown_count == report.concept_count + report.reserved_count
assert report.is_conformant
```

## Current scope

- UTF-8 Markdown discovery, matching `.md` case-insensitively;
- reserved `index.md` and `log.md` handling;
- strict YAML-frontmatter parsing for concept documents, validated with Pydantic
  at the parse boundary so one malformed document cannot abort a run;
- required non-empty `type`;
- stable concept IDs derived from paths;
- Markdown-link extraction and resolution;
- Ibis tables for concepts, reserved documents, and links;
- NetworkX graph projection for traversal, cycles, components, and impact;
- aggregated validation reports.

Profiles, lifecycle/provenance family validation, external resources, and
pluggable Ibis rules are the next milestones.
