---
type: Project
title: okf-tools
description: Relational inspection and validation for Open Knowledge Format bundles
---

# okf-tools

Relational inspection and validation for
[Open Knowledge Format (OKF) v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundles.

`okf-tools` reads an OKF bundle without imposing a domain taxonomy, preserves
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
uv run okf-tools check path/to/bundle
uv run okf-tools check path/to/bundle --require-all-caps-frontmatter
uv run okf-tools inventory path/to/bundle
uv run okf-tools graph path/to/bundle
uv run okf-tools format path/to/bundle
uv run okf-tools format path/to/bundle --write
uv run okf-tools duckdb path/to/bundle knowledge.duckdb
```

The command exits with status `1` only when normative errors exist. Broken
cross-links are warnings because OKF v0.2 explicitly says they do not make a
bundle non-conformant.

## GitHub Actions

Add the repository as a CI check:

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: franklinbaldo/okf-tools@v1
    with:
      path: knowledge
      require-all-caps-frontmatter: "true"
```

The composite action installs a pinned uv version and executes the same
`validate_path()` function used by the Python API, CLI, and MCP server.

Releases are published to PyPI from GitHub Releases through OIDC Trusted
Publishing. No long-lived PyPI token is stored in the repository.

## MCP

```bash
uv run okf-tools serve
uv run okf-tools-mcp
uv run fastmcp run
```

Read-only tools: `check`, `inventory`, `graph`, and `format_check`.

## DuckDB

`okf-tools` is a regular uv-managed Python app; the DuckDB integration is part
of the same package, not a native C++ subproject:

```python
import duckdb

from okf_tools.duckdb import attach_okf

connection = duckdb.connect("knowledge.duckdb")
attach_okf(connection, "knowledge/")

connection.sql("""
    SELECT concept_type, count(*)
    FROM okf.concepts
    GROUP BY concept_type
""").show()
```

The call creates `okf.concepts`, `okf.links`, `okf.reserved`, and
`okf.diagnostics` as ordinary DuckDB tables. Use a new database or schema for
each materialization.

## Python API

```python
from pathlib import Path

from okf_tools import load_bundle, validate_path

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

- UTF-8 Markdown discovery;
- reserved `index.md` and `log.md` handling;
- strict YAML-frontmatter parsing for concept documents;
- required non-empty `type`;
- stable concept IDs derived from paths;
- Markdown-link extraction and resolution;
- Ibis tables for concepts, reserved documents, and links;
- NetworkX graph projection for traversal, cycles, components, and impact;
- aggregated validation reports.

Profiles, lifecycle/provenance family validation, external resources, and
pluggable Ibis rules are the next milestones.
